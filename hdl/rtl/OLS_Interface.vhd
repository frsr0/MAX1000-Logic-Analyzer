  
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
   Gen_I2C_Rd_Len : OUT NATURAL range 0 to 255 := 0;
   Gen_I2C_Dev_R  : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Gen_I2C_Test   : OUT STD_LOGIC := '0';
    Gen_SPI_Test   : OUT STD_LOGIC := '0';
     Armed          : OUT STD_LOGIC := '0';
      Fast_Mode      : OUT STD_LOGIC := '0';
      Continuous_Mode : OUT STD_LOGIC := '0';
      Analog_Mode     : OUT STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
       Analog_Ch0      : OUT NATURAL range 0 to 15 := 0;
       Analog_Ch1      : OUT NATURAL range 0 to 15 := 1;
       Buffer_Full     : IN  STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
       Buffer_Ack      : OUT STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
       Pin_Map_Write   : OUT STD_LOGIC := '0';
        Pin_Map_Channel : OUT NATURAL range 0 to 15 := 0;
        Pin_Map_Pin     : OUT NATURAL range 0 to 31 := 0;
        Debug_Ch0_Enable : OUT STD_LOGIC := '0';
        Schmitt_Enable   : OUT STD_LOGIC := '0';
        Schmitt_Threshold : OUT NATURAL range 0 to 7 := 3;
         Gen_Capture_Active : OUT STD_LOGIC := '0';
         Gen_Start_Ack      : IN  STD_LOGIC := '0';
         Gen_Start_Reject   : IN  STD_LOGIC := '0';
         Gen_Done_Pulse     : IN  STD_LOGIC := '0'

);
END OLS_Interface;

ARCHITECTURE BEHAVIORAL OF OLS_Interface IS

  SIGNAL Run_OLS  : STD_LOGIC := '0';
  SIGNAL dbg_rx_valid_seen : STD_LOGIC := '0';
  SIGNAL Trigger_Mask   : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL Trigger_Values : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL inputs_prev    : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL Divider : NATURAL range 0 to 16777215 := 0;
  SIGNAL Read_Count  : NATURAL := 0;
  SIGNAL Delay_Count : NATURAL := 0;
  SIGNAL Channel_Groups : STD_LOGIC_VECTOR(3 downto 0) := "0000";
  SIGNAL analog_ch0_i     : NATURAL range 0 to 15 := 0;
  SIGNAL analog_ch1_i     : NATURAL range 0 to 15 := 1;
  SIGNAL analog_mode_i    : STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
  SIGNAL SPI_RX_Valid     : STD_LOGIC := '0';
  SIGNAL SPI_RX_Data      : STD_LOGIC_VECTOR (8-1 DOWNTO 0) := (others => '0');
  -- SPI mode only: directly use SPI signals (no UART muxing)
  SIGNAL spi_tx_busy       : STD_LOGIC := '0';
  SIGNAL rx_count_debug    : STD_LOGIC_VECTOR(3 downto 0) := (others => '0');

  -- Generator FIFO depth (matches Signal_Gen.vhd generic)
  constant GEN_FIFO_DEPTH : natural := 256;

  SIGNAL addr : NATURAL := 0;
  SIGNAL wr_ctr : NATURAL range 0 to 18 := 0;

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
  SIGNAL gen_i2c_rd_len_int : NATURAL range 0 to 255 := 0;
  SIGNAL gen_i2c_dev_r_int  : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
   SIGNAL gen_i2c_test_int   : STD_LOGIC := '0';
   SIGNAL gen_spi_test_int   : STD_LOGIC := '0';
   SIGNAL gen_proto_int      : STD_LOGIC := '0';
   SIGNAL gen_baud_div_int   : STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
  SIGNAL fast_mode_i        : STD_LOGIC := '0';
  SIGNAL continuous_mode_i   : STD_LOGIC := '0';
  SIGNAL cont_buf_sel        : NATURAL range 0 to 2 := 0;
  SIGNAL cont_rem            : NATURAL range 0 to 1048576 := 0;
  SIGNAL cont_base_addr      : NATURAL range 0 to 1048576 := 0;
  SIGNAL cont_prefetch       : STD_LOGIC := '0';
  SIGNAL prev_buf_sel        : NATURAL range 0 to 2 := 0;
  SIGNAL buffer_ack_i        : STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
  SIGNAL spi_preamble        : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL spi_preamble_r      : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL spi_tx_ready_i      : STD_LOGIC := '0';
  SIGNAL proto_trig_enable   : STD_LOGIC := '0';
  SIGNAL dbg_pkt_ok_seen     : STD_LOGIC := '0';
  SIGNAL dbg_bad_crc_seen    : STD_LOGIC := '0';
  SIGNAL dbg_bad_frame_seen  : STD_LOGIC := '0';

  SIGNAL ch_mode             : STD_LOGIC := '0';  -- 0=8ch/500k, 1=4ch/4M
  SIGNAL debug_ch0_enable_i  : STD_LOGIC := '0';
  SIGNAL schmitt_enable_i    : STD_LOGIC := '0';
  SIGNAL schmitt_threshold_i : NATURAL range 0 to 7 := 3;
  SIGNAL gen_capture_active_i  : STD_LOGIC := '0';
  SIGNAL gen_capture_done_i    : STD_LOGIC := '0';
  SIGNAL gen_capture_error_i   : STD_LOGIC := '0';
  SIGNAL gen_start_pulse     : STD_LOGIC := '0';
  SIGNAL gen_capture_guard   : NATURAL range 0 to 255 := 0;
  SIGNAL gen_capture_start   : STD_LOGIC := '0';
  type gen_cap_state_t is (GENCAP_IDLE, GENCAP_LOOPBACK_ON, GENCAP_ARM, GENCAP_GUARD, GENCAP_WAIT_BUSY, GENCAP_RUNNING, GENCAP_WAIT_FULL, GENCAP_DONE, GENCAP_ERROR);
  SIGNAL gen_cap_state : gen_cap_state_t := GENCAP_IDLE;
  SIGNAL pipe_depth          : NATURAL range 2 to 8 := 8;
  SIGNAL proto_trig_protocol : STD_LOGIC_VECTOR(1 downto 0) := "00";
  SIGNAL proto_trig_match    : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL proto_trig_bauddiv  : NATURAL range 1 to 65535 := 416;
  SIGNAL proto_trig_channel  : NATURAL range 0 to 7 := 0;
  SIGNAL proto_trig_pulse    : STD_LOGIC := '0';

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
  SIGNAL pkt_payload_last     : STD_LOGIC := '0';
  SIGNAL pkt_ok               : STD_LOGIC := '0';
  SIGNAL pkt_err              : STD_LOGIC := '0';
  SIGNAL pkt_err_bad_crc      : STD_LOGIC := '0';
  SIGNAL pkt_err_bad_sync     : STD_LOGIC := '0';
  SIGNAL pkt_err_oversize     : STD_LOGIC := '0';
  -- First 8 payload bytes captured for quick dispatch access
  TYPE payload_header_t IS ARRAY(0 TO 7) OF STD_LOGIC_VECTOR(7 DOWNTO 0);
  SIGNAL rx_payload_header    : payload_header_t := (others => (others => '0'));
  SIGNAL rx_header_idx        : NATURAL range 0 TO 7 := 0;
  SIGNAL rx_header_len        : NATURAL range 0 TO MAX_RX_PAYLOAD_BYTES := 0;
  -- TX streaming interface
  SIGNAL pkt_tx_byte          : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL pkt_tx_valid         : STD_LOGIC := '0';
  SIGNAL pkt_tx_done          : STD_LOGIC := '0';
  SIGNAL pkt_tx_payload_ready : STD_LOGIC := '0';
  SIGNAL pkt_idle_byte        : STD_LOGIC := '0';
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
  SIGNAL block_rd_addr        : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL block_rd_state       : NATURAL range 0 to 6 := 0;
  SIGNAL block_rd_wc          : NATURAL range 0 to 256 := 0;
  SIGNAL block_addr_reg       : NATURAL range 0 to 1048575 := 0;
  SIGNAL sig_rd_pend_d1       : STD_LOGIC := '0';
  TYPE block_buf_t IS ARRAY(0 TO 255) OF STD_LOGIC_VECTOR(31 DOWNTO 0);
  SIGNAL block_buf            : block_buf_t := (others => (others => '0'));

  -- 21-cycle bit-serial divider for /3 (replaces 58-level lpm_divide)
  SIGNAL div3_shift   : STD_LOGIC_VECTOR(20 downto 0) := (others => '0');
  SIGNAL div3_acc     : NATURAL range 0 to 7 := 0;
  SIGNAL div3_result  : NATURAL range 0 to 1048576 := 0;
  SIGNAL div3_count   : NATURAL range 0 to 31 := 0;
  SIGNAL div3_busy    : STD_LOGIC := '0';
  SIGNAL div3_pending : STD_LOGIC := '0';
  SIGNAL samples_div3  : NATURAL range 0 to 1048576 := 0;
  SIGNAL samples_2div3 : NATURAL range 0 to 1048576 := 0;
  COMPONENT Protocol_Trigger IS
  port (
    CLK          : in  std_logic;
    Inputs       : in  std_logic_vector(7 downto 0);
    Enable       : in  std_logic;
    Protocol     : in  std_logic_vector(1 downto 0);
    Match_Value  : in  std_logic_vector(7 downto 0);
    Baud_Div     : in  natural range 1 to 65535;
    UART_Channel : in  natural range 0 to 7;
    Trigger      : out std_logic
  );
  END COMPONENT;
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
    payload_last   : OUT STD_LOGIC;
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
    tx_valid    : OUT STD_LOGIC;
    tx_done     : OUT STD_LOGIC;
    idle_byte   : OUT STD_LOGIC
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
    VARIABLE Thread23 : NATURAL range 0 to 6 := 0;
    VARIABLE Thread26 : NATURAL range 0 to 34 := 0;
    VARIABLE Thread30 : NATURAL range 0 to 3 := 0;
    VARIABLE Thread31 : NATURAL range 0 to 4 := 0;
    VARIABLE next_sel : NATURAL range 0 to 2 := 0;
  BEGIN
  IF RISING_EDGE(CLK) THEN
    div3_pending <= '0';
    Pin_Map_Write <= '0';
    gen_reg_load_req <= '0';
    IF disp_arm = '1' THEN
      Run_OLS <= '1';
      Thread23 := 0;
    END IF;
    IF disp_abort = '1' THEN
      Run_OLS <= '0';
      Run <= '0';
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
          analog_mode_i(0) <= disp_reg_wdata(3);
        WHEN REG_FAST_MODE =>
          fast_mode_i <= disp_reg_wdata(0);
        WHEN REG_CONT_MODE =>
          continuous_mode_i <= disp_reg_wdata(0);
          IF disp_reg_wdata(0) = '1' THEN
            cont_buf_sel <= 0;
            cont_base_addr <= 0;
            cont_prefetch <= '0';
            IF div3_busy = '0' THEN
              cont_rem <= samples_div3;
            ELSE
              cont_rem <= Read_Count / 4;
            END IF;
            Run_OLS <= '1';
          END IF;
        WHEN REG_GEN_PROTO =>
          gen_proto_int <= disp_reg_wdata(0);
        WHEN REG_GEN_BAUD =>
          gen_baud_div_int <= disp_reg_wdata(15 downto 0);
        WHEN REG_GEN_PINS =>
          gen_tx_pin_int <= TO_INTEGER(UNSIGNED(disp_reg_wdata(4 downto 0)));
          gen_scl_pin_int <= TO_INTEGER(UNSIGNED(disp_reg_wdata(12 downto 8)));
        WHEN REG_GEN_DATA =>
          -- Legacy CMD_I2C_TEST (0xA7) layout when upper bytes are set.
          -- Low-byte-only writes load the gen FIFO without touching mode flags.
          IF disp_reg_wdata(31 downto 8) = x"000000" THEN
            gen_reg_load_byte <= disp_reg_wdata(7 downto 0);
            gen_reg_load_req <= '1';
          ELSE
            gen_i2c_test_int <= disp_reg_wdata(0);
            gen_spi_test_int <= disp_reg_wdata(1);
            gen_i2c_rd_len_int <= TO_INTEGER(UNSIGNED(disp_reg_wdata(15 downto 8)));
            gen_i2c_dev_r_int <= disp_reg_wdata(23 downto 16);
          END IF;

        WHEN REG_DEBUG_CH0_ENABLE =>
          debug_ch0_enable_i <= disp_reg_wdata(0);
        WHEN REG_SCHMITT_ENABLE =>
          schmitt_enable_i <= disp_reg_wdata(0);
        WHEN REG_SCHMITT_THRESHOLD =>
          schmitt_threshold_i <= TO_INTEGER(UNSIGNED(disp_reg_wdata(2 downto 0)));
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
        IF (UNSIGNED(Trigger_Mask(29 downto 0)) = 0 AND proto_trig_enable = '0') THEN
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
          -- Protocol decode trigger (independent of mask bits)
          IF proto_trig_enable = '1' AND proto_trig_pulse = '1' THEN
            Run <= '1';
          END IF;
        END IF;
        IF Run_OLS = '1' THEN
          inputs_prev <= Inputs;
        END IF;
    END IF;
      IF (Full = '1' OR Run = '1') THEN
        CASE (Thread23) IS
          WHEN 0 =>
            IF continuous_mode_i = '1' THEN
              addr <= cont_base_addr;
            ELSE
              addr <= 0;
            END IF;
            cont_prefetch <= '0';
            Thread23 := 1;
          WHEN 1 =>
            IF continuous_mode_i = '1' THEN
              IF fast_mode_i = '1' THEN
                -- Fast mode continuous: single buffer, no prefetch
                IF cont_rem > 0 THEN
                  Thread23 := 2;
                ELSE
                  Thread23 := 4;
                END IF;
              ELSIF cont_prefetch = '1' THEN
                -- Prefetch was primed in previous cycle: ack completed buffer, read next
                buffer_ack_i <= (others => '0');
                buffer_ack_i(prev_buf_sel) <= '1';
                cont_prefetch <= '0';
                Thread26 := 0;
                Thread23 := 2;
              ELSIF cont_rem > 1 THEN
                Thread23 := 2;  -- normal read (more than 1 addr left)
              ELSIF cont_rem = 1 THEN
                -- Last address: try to prefetch next buffer
                next_sel := (cont_buf_sel + 1) mod 3;
                IF Buffer_Full(next_sel) = '1' THEN
                  prev_buf_sel <= cont_buf_sel;
                  cont_prefetch <= '1';
                  cont_buf_sel <= next_sel;
                  CASE next_sel IS
                    WHEN 0 => cont_base_addr <= 0;
                    WHEN 1 => cont_base_addr <= samples_div3;
                    WHEN 2 => cont_base_addr <= samples_2div3;
                  END CASE;
                END IF;
                Thread23 := 2;
              ELSE
                Thread23 := 4;  -- buffer done, no prefetch
              END IF;
            ELSIF ( addr < Samples) THEN 
              Thread23 := Thread23 + 1;
            ELSE
              Thread23 := Thread23 + 2;
            END IF;
          WHEN (1+1) =>
            CASE (Thread26) IS
      WHEN 0 =>
        IF block_rd_state = 0 THEN
          Address <= addr;
        END IF;
        Thread26 := 1;
      WHEN 1 to 29 =>
        Thread26 := Thread26 + 1;
      WHEN 30 =>
        wr_ctr <= 0;
        Thread26 := 31;
      WHEN 31 =>
        IF ( wr_ctr < 4) THEN 
          Thread26 := Thread26 + 1;
        ELSE
          Thread26 := Thread26 + 2;
        END IF;
      WHEN 32 =>
        CASE (Thread30) IS
                  WHEN 0 =>
                    IF (Channel_Groups(wr_ctr) = '0') THEN 
                      Thread30 := Thread30 + 1;
                    ELSE
                      Thread30 := Thread30 + 2;
                    END IF;
                        WHEN (0+1) =>
                    CASE (Thread31) IS
                      WHEN 0 =>
                        -- SPI mode: wait for SPI to be ready
                        IF spi_tx_ready_i = '1' THEN
                          Thread31 := 0;
                          Thread30 := 2;
                        END IF;
                      WHEN others => Thread31 := 0;
                    END CASE;
        WHEN 2 =>
          wr_ctr <= wr_ctr + 1;
          Thread30 := 0;
          Thread26 := 31;
          -- Prefetch: change Address to next buffer's base after last byte sent
          IF cont_prefetch = '1' AND wr_ctr = 0 AND block_rd_state = 0 THEN
            Address <= cont_base_addr;
          END IF;
        WHEN others => Thread30 := 0;
      END CASE;
    WHEN 33 =>
      addr <= addr + 1;
      IF continuous_mode_i = '1' AND cont_rem > 0 THEN
        cont_rem <= cont_rem - 1;
      END IF;
      Thread26 := 0;
      Thread23 := 1;
              WHEN others => Thread26 := 0;
            END CASE;
          WHEN 3 =>
            IF continuous_mode_i = '1' THEN
              Thread23 := 4;  -- continuous: ack and continue
            ELSE
              Run_OLS <= '0';
              Run <= '0';
              Thread23 := 6;  -- non-continuous: idle (was 0, looped into second all-zero readout)
            END IF;
          WHEN 6 =>
            null;  -- idle after non-continuous single-shot readout
          WHEN 4 =>
            -- Buffer read complete: ack the buffer we just finished
            buffer_ack_i <= (others => '0');
            buffer_ack_i(cont_buf_sel) <= '1';
            IF fast_mode_i = '1' THEN
              -- Fast mode: single BRAM buffer, stay on 0, reload 1024 words
              cont_base_addr <= 0;
              cont_buf_sel <= 0;
              cont_rem <= 1024;
            ELSE
              -- SDRAM: cycle to next buffer (0→1→2→0)
              CASE cont_buf_sel IS
                WHEN 0 => cont_base_addr <= samples_div3;  cont_buf_sel <= 1;
                WHEN 1 => cont_base_addr <= samples_2div3;  cont_buf_sel <= 2;
                WHEN 2 => cont_base_addr <= 0;  cont_buf_sel <= 0;
              END CASE;
              cont_rem <= samples_div3;
            END IF;
            Thread23 := 5;
          WHEN 5 =>
            buffer_ack_i <= (others => '0');
            -- Check if next buffer is already full
            IF (cont_buf_sel = 0 AND Buffer_Full(0) = '1') OR
               (cont_buf_sel = 1 AND Buffer_Full(1) = '1') OR
               (cont_buf_sel = 2 AND Buffer_Full(2) = '1') THEN
              addr <= cont_base_addr;
              Thread26 := 0;
              Thread23 := 2;
            END IF;
          WHEN others => Thread23 := 0;
        END CASE;
      END IF;
    -- ── Block read state machine (for CMD_READ_CAPTURE) ──────────────
    sig_rd_pend_d1 <= block_rd_pending;
    IF block_rd_pending = '1' AND sig_rd_pend_d1 = '0' THEN
      block_addr_reg <= TO_INTEGER(UNSIGNED(block_rd_addr(31 downto 2)));
      block_rd_wc <= 0;
      block_rd_state <= 1;
    END IF;
    CASE block_rd_state IS
      WHEN 1 =>
        Address <= block_addr_reg + block_rd_wc;
        block_rd_state <= 2;
      WHEN 2 =>
        block_rd_state <= 3;
      WHEN 3 =>
        block_rd_state <= 4;
      WHEN 4 =>
        block_buf(block_rd_wc) <= Outputs;
        IF block_rd_wc < 255 THEN
          block_rd_wc <= block_rd_wc + 1;
          block_rd_state <= 1;
        ELSE
          block_rd_state <= 5;
        END IF;
      WHEN 5 =>
        block_rd_ack <= '1';
        block_rd_state <= 6;
      WHEN 6 =>
        IF block_rd_pending = '0' THEN
          block_rd_ack <= '0';
          block_rd_state <= 0;
        END IF;
      WHEN OTHERS =>
        null;
    END CASE;
  END IF;
  END PROCESS;

  -- Generator load/start: dedicated process so FIFO writes and Start are not
  -- lost across the dispatch/streaming handshake timing.
  gen_ctl: PROCESS (CLK)
  BEGIN
    IF RISING_EDGE(CLK) THEN
      Gen_Load_We <= '0';
      Gen_Start <= '0';

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
    VARIABLE guard_var : NATURAL range 0 to 255 := 0;
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
              guard_var := 48;
              gen_cap_state <= GENCAP_GUARD;
            END IF;
          WHEN GENCAP_GUARD =>
            IF guard_var > 0 THEN
              guard_var := guard_var - 1;
            ELSE
              gen_start_pulse <= '1';
              gen_cap_state <= GENCAP_WAIT_BUSY;
            END IF;
          WHEN GENCAP_WAIT_BUSY =>
            IF Gen_Busy = '1' OR Gen_Start_Ack = '1' THEN
              gen_capture_active_i <= '1';
              gen_cap_state <= GENCAP_RUNNING;
            END IF;
          WHEN GENCAP_RUNNING =>
            IF Gen_Busy = '0' AND gen_busy_d = '1' THEN
              gen_cap_state <= GENCAP_WAIT_FULL;
            END IF;
          WHEN GENCAP_WAIT_FULL =>
            IF Full = '1' THEN
              gen_capture_active_i <= '0';
              gen_capture_done_i <= '1';
              gen_cap_state <= GENCAP_DONE;
            END IF;
          WHEN GENCAP_DONE =>
            NULL;
          WHEN GENCAP_ERROR =>
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
          samples_div3  <= div3_result;
          samples_2div3 <= div3_result + div3_result;
        end if;
      end if;
    end if;
  end process;

  -- Gen-FIFO depth invariant (simulation-only check)
  -- pragma translate_off
  assert GEN_FIFO_DEPTH > 0 report "GEN_FIFO_DEPTH must be > 0" severity failure;
  -- pragma translate_on

  pipe_depth <= 8 when ch_mode = '0' else 4;

  -- Bring-up/status preamble: run flags plus sticky SPI packet diagnostics.
  -- Registered on sys_clk before crossing to fast_clk SPI slave domain.
  spi_preamble <= Run & Run_OLS & Full & '1' &
                  dbg_rx_valid_seen & dbg_pkt_ok_seen &
                  dbg_bad_crc_seen & dbg_bad_frame_seen;
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
  Gen_I2C_Rd_Len <= gen_i2c_rd_len_int;
  Gen_I2C_Dev_R  <= gen_i2c_dev_r_int;
  Gen_I2C_Test   <= gen_i2c_test_int;
  Gen_SPI_Test   <= gen_spi_test_int;
  Fast_Mode      <= fast_mode_i;
  Continuous_Mode <= continuous_mode_i;
  Analog_Mode <= analog_mode_i;
  Analog_Ch0 <= analog_ch0_i;
  Analog_Ch1 <= analog_ch1_i;
  Buffer_Ack      <= buffer_ack_i;
  Armed          <= Run_OLS;
  Debug_Ch0_Enable <= debug_ch0_enable_i;
  Schmitt_Enable   <= schmitt_enable_i;
  Schmitt_Threshold <= schmitt_threshold_i;
  Gen_Capture_Active <= gen_capture_active_i;
  -- Pin_Map_Write is driven from the main process (default low, pulsed in CMD_PIN_MAP handler)

  Proto_Trigger1 : Protocol_Trigger
  PORT MAP (
    CLK          => CLK,
    Inputs       => Inputs(7 downto 0),
    Enable       => proto_trig_enable,
    Protocol     => proto_trig_protocol,
    Match_Value  => proto_trig_match,
    Baud_Div     => proto_trig_bauddiv,
    UART_Channel => proto_trig_channel,
    Trigger      => proto_trig_pulse
  );

  Interface_Mode <= '1';

  -- Mux TX_Data between UART path (UART mode) and packet protocol (SPI mode)
  spi_tx_mux : block
    signal spi_tx_tdata : std_logic_vector(7 downto 0) := x"FF";
  begin
    spi_tx_tdata <= pkt_tx_byte;
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
    payload_last   => pkt_payload_last,
    cmd_active     => pkt_cmd_active,
    packet_ok   => pkt_ok,
    packet_err  => pkt_err,
    err_bad_crc  => pkt_err_bad_crc,
    err_bad_sync => pkt_err_bad_sync,
    err_oversize => pkt_err_oversize
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
        end if;
        rx_header_idx <= rx_header_idx + 1;
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
    type state_t is (IDLE, EXEC, WAIT_BLOCK, BUILD_RSP, FEED_TX, WAIT_TX);
    variable st : state_t := IDLE;
    variable rsp_seq_v : std_logic_vector(7 downto 0) := (others => '0');
    variable rsp_stat_v : std_logic_vector(7 downto 0) := ST_OK;
    variable rsp_len_v : natural range 0 to MAX_TX_PAYLOAD_BYTES := 0;
    -- Small response buffer (8 bytes covers all non-block-read responses)
    type rspbuf_t is array(0 to 15) of std_logic_vector(7 downto 0);
    variable rsp_buf : rspbuf_t;
    variable rsp_buf_len : natural range 0 to 15 := 0;
    variable rsp_buf_idx : natural range 0 to 15 := 0;
    variable reg_val : std_logic_vector(31 downto 0) := (others => '0');
    -- Block-read streaming state
    variable blk_wc : natural range 0 to 255 := 0;  -- word counter
    variable blk_bc : natural range 0 to 3 := 0;    -- byte-within-word counter
    -- Flag: true when payload comes from block_buf, not rsp_buf
    variable feeding_block : boolean := false;
    variable feed_wait_ready_low : boolean := false;
    variable block_last_v : boolean := false;
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
              elsif Full = '1' then
                rsp_stat_v := ST_CAPTURE_DONE;
              else
                rsp_stat_v := ST_CAPTURE_IDLE;
              end if;
              rsp_buf(0) := Gen_Fifo_Count;
              rsp_buf(2) := gen_load_events;
              rsp_buf(1)(0) := Gen_Busy;
              rsp_buf(1)(1) := gen_start_req;
              rsp_buf(1)(7 downto 2) := (others => '0');
              rsp_buf_len := 3;
              rsp_len_v := 3;
              st := BUILD_RSP;

            when CMD_GET_METADATA =>
              rsp_buf(0) := x"10";
              rsp_buf(1) := x"10";  -- 16 channels
              rsp_buf(2) := x"00";
              rsp_buf(3) := x"F0";
              rsp_buf(4) := x"01";
              -- bytes 5-8: SAMPLE_CLK_HZ in kHz, little-endian uint32
              rsp_buf(5) := std_logic_vector(to_unsigned(SAMPLE_CLK_HZ / 1000, 32))(7 downto 0);
              rsp_buf(6) := std_logic_vector(to_unsigned(SAMPLE_CLK_HZ / 1000, 32))(15 downto 8);
              rsp_buf(7) := std_logic_vector(to_unsigned(SAMPLE_CLK_HZ / 1000, 32))(23 downto 16);
              rsp_buf(8) := std_logic_vector(to_unsigned(SAMPLE_CLK_HZ / 1000, 32))(31 downto 24);
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

            when CMD_READ_CAPTURE =>
              if rx_header_len >= 4 then
                block_rd_addr(7 downto 0)   <= rx_payload_header(0);
                block_rd_addr(15 downto 8)  <= rx_payload_header(1);
                block_rd_addr(23 downto 16) <= rx_payload_header(2);
                block_rd_addr(31 downto 24) <= rx_payload_header(3);
                block_rd_pending <= '1';
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
                    reg_val(3) := analog_mode_i(0);
                  when REG_CONT_MODE =>
                    reg_val(0) := continuous_mode_i;
                  when REG_GEN_PROTO =>
                    reg_val(0) := gen_proto_int;
                  when REG_GEN_BAUD =>
                    reg_val(15 downto 0) := gen_baud_div_int;
                  when REG_GEN_PINS =>
                    reg_val(4 downto 0) := std_logic_vector(to_unsigned(gen_tx_pin_int, 5));
                    reg_val(12 downto 8) := std_logic_vector(to_unsigned(gen_scl_pin_int, 5));
                  when REG_GEN_DATA =>
                    reg_val(0) := gen_i2c_test_int;
                    reg_val(1) := gen_spi_test_int;
                    reg_val(15 downto 8) := std_logic_vector(to_unsigned(gen_i2c_rd_len_int, 8));
                    reg_val(23 downto 16) := gen_i2c_dev_r_int;
                  when REG_DEBUG_CH0_ENABLE =>
                    reg_val(0) := debug_ch0_enable_i;
                  when REG_SCHMITT_ENABLE =>
                    reg_val(0) := schmitt_enable_i;
                  when REG_SCHMITT_THRESHOLD =>
                    reg_val(2 downto 0) := std_logic_vector(to_unsigned(schmitt_threshold_i, 3));
                  when others => null;
                end case;
                rsp_buf(0) := reg_val(7 downto 0);
                rsp_buf(1) := reg_val(15 downto 8);
                rsp_buf(2) := reg_val(23 downto 16);
                rsp_buf(3) := reg_val(31 downto 24);
                rsp_buf_len := 4;
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
            rsp_len_v := 1024;
            block_rd_pending <= '0';
            blk_wc := 0;
            blk_bc := 0;
            feeding_block := true;
            st := BUILD_RSP;
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
              -- Stream from block_buf (256 x 32-bit = 1024 bytes)
              disp_tx_payload_in <= block_buf(blk_wc)(blk_bc * 8 + 7 downto blk_bc * 8);
              disp_tx_payload_vld <= '1';
              feed_wait_ready_low := true;
              block_last_v := (blk_wc = 255 and blk_bc = 3);
              if block_last_v then
                st := WAIT_TX;
              else
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
            st := IDLE;
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
    tx_valid    => pkt_tx_valid,
    tx_done     => pkt_tx_done,
    idle_byte   => pkt_idle_byte
  );



  -- Debug: rising-edge detect on SPI_RX_Valid (sys_clk domain)
  rx_valid_edge: process(CLK)
    variable rv_s1 : std_logic := '0';
    variable rv_s2 : std_logic := '0';
  begin
    if rising_edge(CLK) then
      rv_s2 := rv_s1;
      rv_s1 := SPI_RX_Valid;
      if rv_s1 = '1' and rv_s2 = '0' then
        dbg_rx_valid_seen <= '1';
      end if;
      if pkt_ok = '1' then
        dbg_pkt_ok_seen <= '1';
      end if;
      if pkt_err_bad_crc = '1' then
        dbg_bad_crc_seen <= '1';
      end if;
      if pkt_err_bad_sync = '1' or pkt_err_oversize = '1' then
        dbg_bad_frame_seen <= '1';
      end if;
    end if;
  end process;

END BEHAVIORAL;

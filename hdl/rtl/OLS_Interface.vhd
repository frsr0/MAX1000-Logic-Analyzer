  
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all; 


ENTITY OLS_Interface IS
  GENERIC (
      CLK_Frequency   :   INTEGER     := 12000000;    
    Baud_Rate       :   INTEGER     := 115200;      
    Max_Samples     :   NATURAL     := 25000;       
    OS_Rate         :   NATURAL     := 16;          
    Def_IFace       :   NATURAL     := 1            

  );
PORT (
  CLK : IN STD_LOGIC;
  FAST_CLK : IN STD_LOGIC := '0';
  UART_RX      : IN  STD_LOGIC := '1';
  UART_TX      : OUT STD_LOGIC := '1';
  SPI_CS       : IN  STD_LOGIC := '1';
  SPI_MOSI     : IN  STD_LOGIC := '0';
  SPI_MISO     : OUT STD_LOGIC := 'Z';
  Interface_Mode : OUT STD_LOGIC := '0';
  Inputs       : IN  STD_LOGIC_VECTOR(31 downto 0) := (others => '0');  
  Rate_Div     : BUFFER NATURAL range 1 to 150000000 := 12; 
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
       Pin_Map_Pin     : OUT NATURAL range 0 to 31 := 0

);
END OLS_Interface;

ARCHITECTURE BEHAVIORAL OF OLS_Interface IS

  CONSTANT ID : STD_LOGIC_VECTOR(31 downto 0) := x"31414c53";
  SIGNAL command : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL data    : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL Run_OLS  : STD_LOGIC := '0';
  -- Debug: toggle on each CMD_ARM hit
  SIGNAL dbg_rx_valid_seen : STD_LOGIC := '0';  -- toggles on rising edge of SPI_RX_Valid
  SIGNAL dbg_thread38_seen_3 : STD_LOGIC := '0';  -- toggles when Thread38 enters state 3
  SIGNAL Trigger_Mask   : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL Trigger_Values : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL inputs_prev    : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL Divider : NATURAL range 0 to 16777215 := 0;
  SIGNAL Read_Count  : NATURAL := 0;
  SIGNAL Delay_Count : NATURAL := 0;
  SIGNAL Channel_Groups : STD_LOGIC_VECTOR(3 downto 0) := "0000";
  SIGNAL UART_TX_Enable     : STD_LOGIC := '0';
  SIGNAL UART_TX_Busy       : STD_LOGIC := '0';
  SIGNAL UART_TX_Data : STD_LOGIC_VECTOR (8-1 DOWNTO 0) := (others => '0');
  SIGNAL UART_RX_Busy       : STD_LOGIC := '0';
  SIGNAL UART_RX_Data       : STD_LOGIC_VECTOR (8-1 DOWNTO 0) := (others => '0');

  -- Initialize interface mode from Def_IFace generic (1 = SPI, 0 = UART)
  SIGNAL interface_mode_i : STD_LOGIC := '1';
  SIGNAL analog_ch0_i     : NATURAL range 0 to 15 := 0;
  SIGNAL analog_ch1_i     : NATURAL range 0 to 15 := 1;
  SIGNAL analog_mode_i    : STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
  SIGNAL SPI_RX_Valid     : STD_LOGIC := '0';
  SIGNAL SPI_RX_Data      : STD_LOGIC_VECTOR (8-1 DOWNTO 0) := (others => '0');
  -- Muxed signals for UART/SPI mode selection
  SIGNAL effective_TX_Busy : STD_LOGIC := '0';
  SIGNAL effective_RX_Busy : STD_LOGIC := '0';
  SIGNAL effective_RX_Data : STD_LOGIC_VECTOR (8-1 DOWNTO 0) := (others => '0');

  -- Generator FIFO depth (matches Signal_Gen.vhd generic)
  constant GEN_FIFO_DEPTH : natural := 256;

  SIGNAL addr : NATURAL := 0;
  SIGNAL wr_ctr : NATURAL range 0 to 18 := 0;
  SIGNAL blk_mode  : STD_LOGIC := '0';
  SIGNAL gen_start_cnt : NATURAL range 0 to 63 := 0;
  SIGNAL gen_load_cnt  : NATURAL range 0 to 63 := 0;  -- probe
   SIGNAL gen_tx_pin_int  : NATURAL range 0 to 31 := 3;
   SIGNAL gen_scl_pin_int : NATURAL range 0 to 31 := 1;  -- default=1 (CH0 is test counter, can't use 0)
  SIGNAL gen_i2c_rd_len_int : NATURAL range 0 to 255 := 0;
  SIGNAL gen_i2c_dev_r_int  : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
   SIGNAL gen_i2c_test_int   : STD_LOGIC := '0';
   SIGNAL gen_spi_test_int   : STD_LOGIC := '0';
  SIGNAL fast_mode_i        : STD_LOGIC := '0';
  SIGNAL continuous_mode_i   : STD_LOGIC := '0';
  SIGNAL cont_buf_sel        : NATURAL range 0 to 2 := 0;
  SIGNAL cont_rem            : NATURAL range 0 to 1048576 := 0;
  SIGNAL cont_base_addr      : NATURAL range 0 to 1048576 := 0;
  SIGNAL cont_prefetch       : STD_LOGIC := '0';
  SIGNAL prev_buf_sel        : NATURAL range 0 to 2 := 0;
  SIGNAL buffer_ack_i        : STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
  SIGNAL spi_preamble        : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL proto_trig_enable   : STD_LOGIC := '0';
  SIGNAL cmd_was_multibyte   : STD_LOGIC := '0';
  SIGNAL saved_command       : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL ch_mode             : STD_LOGIC := '0';  -- 0=8ch/500k, 1=4ch/4M
  SIGNAL pipe_depth          : NATURAL range 2 to 8 := 8;
  SIGNAL proto_trig_protocol : STD_LOGIC_VECTOR(1 downto 0) := "00";
  SIGNAL proto_trig_match    : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL proto_trig_bauddiv  : NATURAL range 1 to 65535 := 416;
  SIGNAL proto_trig_channel  : NATURAL range 0 to 7 := 0;
  SIGNAL proto_trig_pulse    : STD_LOGIC := '0';
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
  COMPONENT UART_Interface IS
  GENERIC (
      CLK_Frequency   :   INTEGER     := 12000000;    
    Baud_Rate       :   INTEGER     := 19200;       
    OS_Rate         :   INTEGER     := 16;          
    D_Width         :   INTEGER     := 8;           
    Parity          :   INTEGER     := 0;           
    Parity_EO       :   STD_LOGIC   := '0'         

  );
  PORT (
    CLK : IN STD_LOGIC;
    Reset       : IN    STD_LOGIC := '0';                       
    RX          : IN    STD_LOGIC := '1';                       
    TX          : OUT   STD_LOGIC := '1';                       
    TX_Enable   : IN    STD_LOGIC := '0';                       
    TX_Busy     : OUT   STD_LOGIC := '0';                       
    TX_Data     : IN    STD_LOGIC_VECTOR(D_Width-1 DOWNTO 0) := (others => '0');    
    RX_Busy     : OUT   STD_LOGIC := '0';                       
    RX_Data     : OUT   STD_LOGIC_VECTOR(D_Width-1 DOWNTO 0) := (others => '0');    
    RX_Error    : OUT   STD_LOGIC := '0'                       

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
    PipeDepth  : IN  NATURAL range 2 to 8 := 8;
    RX_Data    : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    RX_Valid   : OUT STD_LOGIC := '0'
  );
  END COMPONENT;

BEGIN
  PROCESS (CLK)  
    VARIABLE ctr : INTEGER range 0 to 4 := 0;
    VARIABLE Thread23 : NATURAL range 0 to 6 := 0;
    VARIABLE Thread26 : NATURAL range 0 to 34 := 0;
    VARIABLE Thread30 : NATURAL range 0 to 3 := 0;
    VARIABLE Thread31 : NATURAL range 0 to 4 := 0;
    VARIABLE Thread38 : NATURAL range 0 to 7 := 0;
    VARIABLE Thread44 : NATURAL range 0 to 40 := 0;
    VARIABLE Thread45 : NATURAL range 0 to 4 := 0;
    VARIABLE Thread49 : NATURAL range 0 to 2 := 0;
    VARIABLE Thread51 : NATURAL range 0 to 5 := 0;
    VARIABLE blk_len  : NATURAL range 0 to GEN_FIFO_DEPTH := 0;
    VARIABLE next_sel : NATURAL range 0 to 2 := 0;
  BEGIN
  IF RISING_EDGE(CLK) THEN
    Gen_Load_We <= '0';
    Gen_Start <= '0';
    div3_pending <= '0';
    Pin_Map_Write <= '0';
    IF (Divider < CLK_Frequency) THEN
      Rate_Div <= Divider + 1;
    ELSE
      Rate_Div <= CLK_Frequency;
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
    IF (Read_Count > Delay_Count) THEN
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
      IF (Full = '1' AND (interface_mode_i = '1' OR Run = '1')) THEN
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
        Address <= addr;
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
                        UART_TX_Data <= Outputs((wr_ctr+1)*8-1 downto wr_ctr*8);
                        UART_TX_Enable <= '1';
                        Thread31 := 1;
                      WHEN 1 =>
                        IF (effective_TX_Busy = '0') THEN
                        ELSE
                          Thread31 := Thread31 + 1;
                        END IF;
                      WHEN 2 =>
                        UART_TX_Enable <= '0';
                        Thread31 := 3;
                      WHEN 3 =>
                        IF (effective_TX_Busy = '1') THEN
                        ELSE
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
          IF cont_prefetch = '1' AND wr_ctr = 0 THEN
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
    CASE (Thread38) IS
      WHEN 0 =>
        Thread38 := 1;
      WHEN 1 =>
        IF (effective_RX_Busy = '0') THEN
        ELSE
          Thread38 := Thread38 + 1;
        END IF;
      WHEN 2 =>
        IF (effective_RX_Busy = '1') THEN
        ELSE
          Thread38 := Thread38 + 1;
        END IF;
      WHEN 3 =>
        IF (blk_mode = '1') THEN
          Gen_Load_Byte <= effective_RX_Data;
          gen_load_cnt <= 1;
          IF (blk_len > 0) THEN
            blk_len := blk_len - 1;
          END IF;
          IF (blk_len = 0) THEN
            blk_mode <= '0';
          END IF;
          Thread38 := 0;
        ELSE
          -- Accumulate data bytes for multi-byte commands at byte-receive time
           IF (cmd_was_multibyte = '1' AND ctr < 4) OR 
              (ctr < 4 AND command(7) = '1' AND effective_RX_Data(7) = '1') THEN
             data((ctr+1)*8-1 downto ctr*8) <= effective_RX_Data;
             ctr := ctr + 1;
            -- After all 4 bytes, force dispatch
            IF ctr = 4 THEN
              Thread44 := 7;
              Thread45 := 0;
            END IF;
          END IF;
          command <= effective_RX_Data;
          -- Debug: toggles when any byte is received (Thread38 enters state 3)
          dbg_thread38_seen_3 <= NOT dbg_thread38_seen_3;
          -- Debug: capture received byte in UART_TX_Data for readback
          UART_TX_Data <= effective_RX_Data;
          Thread38 := 4;
        END IF;
      WHEN 4 =>
        IF command = x"A1" THEN
          -- GEN_STRT: dispatch directly, bypass accumulate
          cmd_was_multibyte <= '0';
          Thread38 := Thread38 + 1;  -- to 5
        ELSIF Thread44 = 0 AND cmd_was_multibyte = '0' THEN
          saved_command <= command;
          cmd_was_multibyte <= command(7);
          IF command(7) = '0' THEN
            Thread38 := Thread38 + 1;  -- to 5 (dispatch)
          ELSE
            ctr := 0;
            Thread38 := Thread38 + 2;  -- to 6 (accumulate)
          END IF;
        ELSIF Thread44 = 0 AND cmd_was_multibyte = '1' THEN
          -- Single-byte command (bit7=0) arriving after a multi-byte command.
          -- Must go through dispatch (Thread38=5), not accumulate (Thread38=6).
          cmd_was_multibyte <= '0';
          IF command(7) = '0' THEN
            Thread44 := 0;
            Thread38 := Thread38 + 1;  -- to 5 for dispatch
          ELSE
            ctr := 0;
            Thread38 := Thread38 + 2;  -- to 6 for accumulate
          END IF;
        ELSIF Thread44 = 18 OR Thread44 = 19 THEN
          Thread38 := Thread38 + 1;
        ELSIF cmd_was_multibyte = '1' AND Thread44 <= 3 THEN
          -- Data byte or new command arriving while accumulate is in setup
          -- (Thread44 still 0-3).  Use command(7) to decide: bit7=0 means
          -- it's a new single-byte command (ARM, Reset); bit7=1 is a data byte.
          -- Must reset Thread44 to 0 so dispatch hits WHEN 0, not the reset handler at WHEN 1.
          cmd_was_multibyte <= '0';
          IF command(7) = '0' THEN
            Thread44 := 0;
            Thread38 := Thread38 + 1;  -- to 5 (dispatch)
          ELSE
            ctr := 0;
            Thread38 := Thread38 + 2;  -- to 6 (accumulate)
          END IF;
        ELSIF cmd_was_multibyte = '1' THEN
          -- Data byte arriving during accumulate (Thread44 >= 4).
          ctr := 0;
          Thread38 := Thread38 + 2;
        ELSE
          Thread38 := Thread38 + 1;
        END IF;
      WHEN (4+1) =>
        CASE (Thread44) IS
          WHEN 0 =>
            CASE (command) IS
              WHEN x"00" =>
                -- CMD_RESET: route through Thread44=1 to clear Run_OLS etc.
                Thread44 := Thread44 + 1;
              WHEN x"01" =>
                -- CMD_ARM: inline Run_OLS to bypass the WHEN 2 handler
                UART_TX_Data <= x"AA";
                Run_OLS <= '1';
                IF continuous_mode_i = '0' THEN
                  Thread23 := 0;
                END IF;
                Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
              WHEN x"02" =>
                Thread44 := Thread44 + 3;
              WHEN x"03" =>
                Thread44 := 19;
              WHEN x"04" =>
                Thread44 := 18;
              WHEN x"05" =>
                Thread44 := Thread44 + 7;  -- blk_mode entry
              WHEN x"06" =>
                Thread44 := Thread44 + 8;  -- proto select
              WHEN x"A1" =>
                gen_start_cnt <= 3;
                Gen_Start <= '1';  -- direct pulse
                Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
              WHEN x"AF" =>
                gen_spi_test_int <= '1';
                Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
              WHEN x"11" =>
                Thread44 := Thread44 + 4;
              WHEN x"13" =>
                Thread44 := Thread44 + 5;
              WHEN others =>
                Thread44 := Thread44 + 6;
            END CASE;
          WHEN 1 =>
            Run_OLS <= '0';
            Run <= '0';
            continuous_mode_i <= '0';
            ch_mode <= '0';
            analog_mode_i <= (others => '0');
            analog_ch0_i <= 0;
            analog_ch1_i <= 1;
            saved_command <= (others => '0');
            cmd_was_multibyte <= '0';
            Trigger_Mask <= (others => '0');
            proto_trig_enable <= '0';
            -- Default generator config: baud=0 defers to Signal_Gen constant; caller must set CMD_GEN_BAUD explicitly
            Gen_Baud_Div <= (others => '0');
            Gen_Proto <= '0';
            gen_spi_test_int <= '0';
            blk_mode <= '0';
            interface_mode_i <= '1';  -- reset to SPI mode
            Thread23 := 0;
            Thread26 := 0;
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 2 =>
            UART_TX_Data <= x"BB";
            Run_OLS <= '1';
            IF continuous_mode_i = '0' THEN
              Thread23 := 0;
            END IF;
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;

              Thread45 := 0;
          WHEN 3 =>
                CASE (Thread49) IS
                  WHEN 0 =>
                    wr_ctr <= 4;
                    Thread49 := 1;
                  WHEN 1 =>
                    IF ( wr_ctr > 0) THEN 
                      Thread49 := Thread49 + 1;
                    ELSE
                      Thread44 := 0;
                    Thread45 := 0;
                    Thread49 := 0;
                    Thread38 := 0;
                    END IF;
                    WHEN (1+1) =>
                    CASE (Thread51) IS
                      WHEN 0 =>
                        UART_TX_Data <= ID(wr_ctr*8-1 downto (wr_ctr-1)*8);
                        UART_TX_Enable <= '1';
                        Thread51 := 1;
                      WHEN 1 =>
                        IF (effective_TX_Busy = '0') THEN
                        ELSE
                          Thread51 := Thread51 + 1;
                        END IF;
                      WHEN 2 =>
                        UART_TX_Enable <= '0';
                        Thread51 := 3;
                      WHEN 3 =>
                        IF (effective_TX_Busy = '1') THEN
                        ELSE
                          Thread51 := Thread51 + 1;
                        END IF;
                      WHEN 4 =>
                        wr_ctr <= wr_ctr - 1;
                        Thread49 := 1;
                        Thread51 := 0;
                      WHEN others => Thread51 := 0;
                    END CASE;
                  WHEN others => Thread49 := 0;
                END CASE;
          WHEN 4 =>
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 5 =>
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 6 =>
            null;
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 7 =>
            blk_mode <= '1';
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 8 =>
            Gen_Proto <= data(0);
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 18 =>
            -- CMD_GEN_LOAD (0xA0, multi-byte): single-cycle load
            IF saved_command = x"A0" THEN
              Gen_Load_Byte <= data(7 downto 0);
              gen_load_cnt <= 1;
              Thread44 := 0; Thread45 := 0; Thread38 := 0;
            ELSE
              -- CMD_METADATA (0x04, single-byte): 18-byte ID string send
              CASE (Thread49) IS
              WHEN 0 =>
                wr_ctr <= 18;
                Thread49 := 1;
              WHEN 1 =>
                IF (wr_ctr > 0) THEN
                  Thread49 := Thread49 + 1;
                ELSE
                  Thread44 := 0; Thread45 := 0; Thread49 := 0; Thread38 := 0;
                END IF;
              WHEN 2 =>
                CASE (Thread51) IS
                  WHEN 0 =>
                    CASE (wr_ctr) IS
                      WHEN 18 => UART_TX_Data <= x"01";
                      WHEN 17 => UART_TX_Data <= x"4F";
                      WHEN 16 => UART_TX_Data <= x"4C";
                      WHEN 15 => UART_TX_Data <= x"53";
                      WHEN 14 => UART_TX_Data <= x"00";
                      WHEN 13 => UART_TX_Data <= x"40";
                      WHEN 12 => UART_TX_Data <= x"10";
                      WHEN 11 => UART_TX_Data <= x"21";
                      WHEN 10 => UART_TX_Data <= x"00";
                      WHEN 9  => UART_TX_Data <= x"10";
                      WHEN 8  => UART_TX_Data <= x"00";
                      WHEN 7  => UART_TX_Data <= x"00";
                      WHEN 6  => UART_TX_Data <= x"23";
                      WHEN 5  => UART_TX_Data <= x"00";
                      WHEN 4  => UART_TX_Data <= x"B7";
                      WHEN 3  => UART_TX_Data <= x"1B";
                      WHEN 2  => UART_TX_Data <= x"00";
                      WHEN 1  => UART_TX_Data <= x"00";
                      WHEN others => null;
                    END CASE;
                    UART_TX_Enable <= '1';
                    Thread51 := 1;
                  WHEN 1 =>
                    IF (effective_TX_Busy = '0') THEN ELSE Thread51 := Thread51 + 1; END IF;
                  WHEN 2 =>
                    UART_TX_Enable <= '0'; Thread51 := 3;
                  WHEN 3 =>
                    IF (effective_TX_Busy = '1') THEN ELSE Thread51 := Thread51 + 1; END IF;
                  WHEN 4 =>
                    wr_ctr <= wr_ctr - 1; Thread49 := 1; Thread51 := 0;
                  WHEN others => Thread51 := 0;
                END CASE;
              WHEN others => Thread49 := 0;
            END CASE;
            END IF;
          WHEN 19 =>
            null;
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 9 =>
            Trigger_Values <= data;
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 10 =>
            null;
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 11 =>
            null;
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 12 =>
            Divider <= TO_INTEGER(UNSIGNED(data(23 downto 0)));
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 13 =>
            null;
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 14 =>
            Channel_Groups <= data(5 downto 2);
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 15 =>
            Delay_Count <= TO_INTEGER(UNSIGNED(data(29 downto 0)));
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 16 =>
            Read_Count <= TO_INTEGER(UNSIGNED(data(29 downto 0)));
            div3_pending <= '1';
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 17 =>
            null;
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 20 =>
            Gen_Baud_Div <= data(15 downto 0);
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 21 =>
            blk_mode <= '1';
            if unsigned(data(31 downto 9)) /= 0 then
              blk_len := 256;
            elsif TO_INTEGER(UNSIGNED(data(8 downto 0))) > 256 then
              blk_len := 256;
            else
              blk_len := TO_INTEGER(UNSIGNED(data(8 downto 0)));
            end if;
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 22 =>
            Gen_Proto <= data(0);
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 23 =>
            null;
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 24 =>
            gen_tx_pin_int <= TO_INTEGER(UNSIGNED(data(7 downto 0))) mod 32;
            gen_scl_pin_int <= TO_INTEGER(UNSIGNED(data(15 downto 8))) mod 32;
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 25 =>
            gen_i2c_test_int <= data(0);
            gen_i2c_rd_len_int <= TO_INTEGER(UNSIGNED(data(15 downto 8)));
            gen_i2c_dev_r_int <= data(23 downto 16);
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 26 =>
            fast_mode_i <= data(0);
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 27 =>
            proto_trig_enable <= data(15);
            proto_trig_protocol <= data(13 downto 12);
            proto_trig_match <= data(7 downto 0);
            proto_trig_channel <= TO_INTEGER(UNSIGNED(data(10 downto 8)));
            proto_trig_bauddiv <= TO_INTEGER(UNSIGNED(data(31 downto 16)));
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 28 =>
            continuous_mode_i <= data(0);
            IF data(0) = '1' THEN
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
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 29 =>
            interface_mode_i <= data(0);
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 30 =>
            analog_ch0_i <= TO_INTEGER(UNSIGNED(data(3 downto 0))) mod 16;
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 31 =>
            analog_mode_i <= "00" & data(0);
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 32 =>
            ch_mode <= data(0);
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 33 =>
            gen_spi_test_int <= data(0);
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 34 =>
            analog_mode_i <= data(2 downto 0);
            analog_ch0_i <= TO_INTEGER(UNSIGNED(data(7 downto 4))) mod 16;
            analog_ch1_i <= TO_INTEGER(UNSIGNED(data(11 downto 8))) mod 16;
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 35 =>
            Pin_Map_Channel <= TO_INTEGER(UNSIGNED(data(7 downto 0)));
            Pin_Map_Pin <= TO_INTEGER(UNSIGNED(data(15 downto 8)));
            Pin_Map_Write <= '1';
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 37 =>
            interface_mode_i <= data(0);
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN 38 =>
            null;
            Thread44 := 0; Thread45 := 0; Thread38 := 0;
          WHEN others => Thread44 := 0; Thread45 := 0; Thread38 := 0;
        END CASE;
      WHEN 6 =>
        CASE (Thread44) IS
          WHEN 0 =>
            ctr := 0;
            Thread44 := 1;
          WHEN 1 =>
            Thread44 := Thread44 + 1;
          WHEN 2 =>
            ctr := 0;
            Thread44 := 3;
          WHEN 3 =>
            IF ( ctr < 4) THEN
              Thread44 := Thread44 + 1;
            ELSE
              Thread44 := Thread44 + 2;
            END IF;
          WHEN (3+1) =>
            CASE (Thread45) IS
              WHEN 0 =>
                Thread45 := 1;
              WHEN 1 =>
                IF (effective_RX_Busy = '0') THEN
                ELSE
                  Thread45 := Thread45 + 1;
                END IF;
              WHEN 2 =>
                IF (effective_RX_Busy = '1') THEN
                ELSE
                  Thread45 := Thread45 + 1;
                END IF;
              WHEN 3 =>
                IF ctr < 4 THEN
                  data((ctr+1)*8-1 downto ctr*8) <= effective_RX_Data;
                  ctr := ctr + 1;
                END IF;
                Thread45 := 0;
                Thread44 := 3;
              WHEN others => Thread45 := 0;
            END CASE;
          WHEN 5 to 6 =>
            Thread44 := Thread44 + 1;
          WHEN 7 =>
            cmd_was_multibyte <= '0';
            Thread38 := 5;  -- default: transition to command execution after accumulate
            CASE (saved_command) IS
              -- Multi-byte prefix (0x11): actual command is first accumulated byte
              WHEN x"11" =>
                IF data(7 downto 0) = x"01" THEN
                  -- Multi-byte CMD_ARM
                  Run_OLS <= '1';
                  Thread23 := 0;
                  Thread44 := 0; Thread45 := 0; Thread38 := 0;
                ELSE
                  Thread44 := Thread44 + 10;
                END IF;
              WHEN x"c0" =>
                Thread44 := Thread44 + 1;
              WHEN x"c1" =>
                Thread44 := Thread44 + 2;
              WHEN x"c2" =>
                Thread44 := Thread44 + 3;
              WHEN x"c3" =>
                Thread44 := Thread44 + 4;
              WHEN x"80" =>
                Thread44 := Thread44 + 5;
              WHEN x"81" =>
                Thread44 := Thread44 + 6;
              WHEN x"82" =>
                Thread44 := Thread44 + 7;
              WHEN x"83" =>
                Thread44 := Thread44 + 8;
              WHEN x"84" =>
                Thread44 := Thread44 + 9;
              WHEN x"A0" =>
                Thread44 := Thread44 + 11;
              WHEN x"A1" =>
                Thread44 := Thread44 + 12;
              WHEN x"A2" =>
                Thread44 := Thread44 + 13;
              WHEN x"A3" =>
                Thread44 := Thread44 + 14;
              WHEN x"A4" =>
                Thread44 := Thread44 + 15;
              WHEN x"A5" =>
                Thread44 := Thread44 + 16;
              WHEN x"A6" =>
                Thread44 := Thread44 + 17;
              WHEN x"A7" =>
                Thread44 := Thread44 + 18;
              WHEN x"A8" =>
                Thread44 := Thread44 + 19;
              WHEN x"A9" =>
                Thread44 := Thread44 + 20;
              WHEN x"AA" =>
                Thread44 := Thread44 + 21;
              WHEN x"AB" =>
                Thread44 := Thread44 + 22;
              WHEN x"AC" =>
                Thread44 := Thread44 + 30;
              WHEN x"AD" =>
                Thread44 := Thread44 + 31;
              WHEN x"BB" =>
                Thread44 := 35;
              WHEN x"B0" =>
                Thread44 := 34;
              WHEN x"AE" =>
                Thread44 := 32;
              WHEN x"AF" =>
                Thread44 := 33;
              WHEN others =>
                Thread44 := Thread44 + 10;
            END CASE;
          WHEN others => Thread44 := 0; Thread45 := 0; Thread38 := 0;
        END CASE;
      WHEN others => Thread38 := 0;
    END CASE;
    -- Stretch Gen_Start to 2 cycles: ensures Signal_Gen sees it (same clock domain)
    IF gen_start_cnt > 0 THEN
      Gen_Start <= '1';
      gen_start_cnt <= gen_start_cnt - 1;
    END IF;
    -- Stretch Gen_Load_We to 2 cycles: ensures Signal_Gen sees it (same clock domain)
    IF gen_load_cnt > 0 THEN
      Gen_Load_We <= '1';
      gen_load_cnt <= gen_load_cnt - 1;
    END IF;
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

  -- SPI preamble byte: zero-waste status on every transaction
  -- bit1 = dbg_rx_valid_seen (toggles on rising edge of SPI_RX_Valid)
  -- bit0 = dbg_thread38_seen_3 (toggles on entry to Thread38=3)
  spi_preamble <= Run & Run_OLS & Full & interface_mode_i &
                  continuous_mode_i & fast_mode_i & dbg_rx_valid_seen & dbg_thread38_seen_3;

  Gen_TX_Pin  <= gen_tx_pin_int;
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
  -- Pin_Map_Write is driven from the main process (default low, pulsed in CMD_PIN_MAP handler)

  UART_Interface1 : UART_Interface
  GENERIC MAP (
      CLK_Frequency => CLK_Frequency,Baud_Rate     => Baud_Rate,OS_Rate       => OS_Rate,D_Width       => 8,Parity        => 0,Parity_EO     => '0'
  ) PORT MAP (
    CLK => CLK,
    Reset         => '0',RX            => UART_RX,TX            => UART_TX,TX_Enable     => UART_TX_Enable,TX_Busy       => UART_TX_Busy,TX_Data       => UART_TX_Data,RX_Busy       => UART_RX_Busy,RX_Data       => UART_RX_Data,RX_Error      => OPEN
  );

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

  Interface_Mode <= interface_mode_i;

  SPI_Slave1 : SPI_Slave2
  PORT MAP (
    sys_clk    => CLK,
    fast_clk   => FAST_CLK,
    reset      => '0',
    SCK        => UART_RX,    -- SPI_SCK on same pin as UART_RX (A4/BDBUS0)
    MOSI       => SPI_MOSI,   -- shared with UART_TX pin (B4/BDBUS1)
    MISO       => SPI_MISO,
    CS_n       => SPI_CS,
    TX_Data    => UART_TX_Data,
    SPI_Preamble   => spi_preamble,
    PipeDepth  => pipe_depth,
    TX_Ready   => open,
    RX_Data    => SPI_RX_Data,
    RX_Valid   => SPI_RX_Valid
  );

  spi_adapter: process(CLK)
  begin
    if rising_edge(CLK) then
      if interface_mode_i = '1' then
        if UART_TX_Enable = '1' then
          effective_TX_Busy <= '1';
        elsif effective_TX_Busy = '1' then
          -- Clear one cycle after Enable: TX_Data has been presented to the
          -- SPI slave's TX_Data port.  The slave latches it on the next
          -- sck_fall (guaranteed to arrive later at any SPI rate supported
          -- by the sys_clk oversampling).  Cannot wait for SPI_RX_Valid
          -- because no SCK edges arrive for status responses — the host
          -- reads TX_Data in the next SPI transaction.
          effective_TX_Busy <= '0';
        end if;
      else
        effective_TX_Busy <= UART_TX_Busy;
      end if;
    end if;
  end process;

  effective_RX_Busy <= UART_RX_Busy when interface_mode_i = '0' else SPI_RX_Valid;
  effective_RX_Data <= UART_RX_Data when interface_mode_i = '0' else SPI_RX_Data;

  -- Debug: rising-edge detect on SPI_RX_Valid (sys_clk domain)
  rx_valid_edge: process(CLK)
    variable rv_s1 : std_logic := '0';
    variable rv_s2 : std_logic := '0';
  begin
    if rising_edge(CLK) then
      rv_s2 := rv_s1;          -- old value (previous cycle)
      rv_s1 := SPI_RX_Valid;   -- new value (this cycle)
      if rv_s1 = '1' and rv_s2 = '0' then
        dbg_rx_valid_seen <= NOT dbg_rx_valid_seen;
      end if;
    end if;
  end process;

END BEHAVIORAL;

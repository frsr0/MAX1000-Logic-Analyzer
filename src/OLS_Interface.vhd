  
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all; 


ENTITY OLS_Interface IS
  GENERIC (
      CLK_Frequency   :   INTEGER     := 12000000;    
    Baud_Rate       :   INTEGER     := 115200;      
    Max_Samples     :   NATURAL     := 25000;       
    OS_Rate         :   NATURAL     := 16          

  );
PORT (
  CLK : IN STD_LOGIC;
  UART_RX      : IN  STD_LOGIC := '1';
  UART_TX      : OUT STD_LOGIC := '1';
  Inputs       : IN  STD_LOGIC_VECTOR(31 downto 0) := (others => '0');  
  Rate_Div     : BUFFER NATURAL range 1 to CLK_Frequency := 12; 
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
   Gen_TX_Pin    : OUT NATURAL range 0 to 7 := 0;
   Gen_SCL_Pin   : OUT NATURAL range 0 to 7 := 0;
   Gen_I2C_Rd_Len : OUT NATURAL range 0 to 255 := 0;
   Gen_I2C_Dev_R  : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
     Gen_I2C_Test   : OUT STD_LOGIC := '0';
     Armed          : OUT STD_LOGIC := '0';
     Fast_Mode      : OUT STD_LOGIC := '0'

);
END OLS_Interface;

ARCHITECTURE BEHAVIORAL OF OLS_Interface IS

  CONSTANT ID : STD_LOGIC_VECTOR(31 downto 0) := x"31414c53";
  SIGNAL command : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL data    : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL Run_OLS  : STD_LOGIC := '0';
  SIGNAL Trigger_Mask   : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL Trigger_Values : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL inputs_prev    : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL Divider : NATURAL range 0 to 16777215 := 0;
  SIGNAL Read_Count  : NATURAL := 0;
  SIGNAL Delay_Count : NATURAL := 0;
  SIGNAL Channel_Groups : STD_LOGIC_VECTOR(3 downto 0) := "0000";
  SIGNAL UART_TX_Enable     : STD_LOGIC := '0';
  SIGNAL UART_TX_Busy       : STD_LOGIC := '0';
  SIGNAL UART_TX_Data       : STD_LOGIC_VECTOR (8-1 DOWNTO 0) := (others => '0');
  SIGNAL UART_RX_Busy       : STD_LOGIC := '0';
  SIGNAL UART_RX_Data       : STD_LOGIC_VECTOR (8-1 DOWNTO 0) := (others => '0');

  SIGNAL addr : NATURAL := 0;
  SIGNAL wr_ctr : NATURAL range 0 to 18 := 0;
  SIGNAL blk_mode  : STD_LOGIC := '0';
  SIGNAL blk_len_s : NATURAL range 0 to 255 := 0;
  SIGNAL gen_start_cnt : NATURAL range 0 to 31 := 0;
  SIGNAL gen_load_cnt  : NATURAL range 0 to 31 := 0;  -- probe
   SIGNAL gen_tx_pin_int  : NATURAL range 0 to 7 := 0;
   SIGNAL gen_scl_pin_int : NATURAL range 0 to 7 := 0;
  SIGNAL gen_i2c_rd_len_int : NATURAL range 0 to 255 := 0;
  SIGNAL gen_i2c_dev_r_int  : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL gen_i2c_test_int   : STD_LOGIC := '0';
  SIGNAL fast_mode_i        : STD_LOGIC := '0';
  SIGNAL proto_trig_enable   : STD_LOGIC := '0';
  SIGNAL proto_trig_protocol : STD_LOGIC_VECTOR(1 downto 0) := "00";
  SIGNAL proto_trig_match    : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL proto_trig_bauddiv  : NATURAL range 1 to 65535 := 416;
  SIGNAL proto_trig_channel  : NATURAL range 0 to 7 := 0;
  SIGNAL proto_trig_pulse    : STD_LOGIC := '0';
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
  
BEGIN
  PROCESS (CLK)  
    VARIABLE ctr : INTEGER range 0 to 4 := 0;
    VARIABLE Thread23 : NATURAL range 0 to 4 := 0;
    VARIABLE Thread26 : NATURAL range 0 to 34 := 0;
    VARIABLE Thread30 : NATURAL range 0 to 3 := 0;
    VARIABLE Thread31 : NATURAL range 0 to 4 := 0;
    VARIABLE Thread38 : NATURAL range 0 to 7 := 0;
    VARIABLE Thread44 : NATURAL range 0 to 27 := 0;
    VARIABLE Thread45 : NATURAL range 0 to 4 := 0;
    VARIABLE Thread49 : NATURAL range 0 to 2 := 0;
    VARIABLE Thread51 : NATURAL range 0 to 5 := 0;
    VARIABLE blk_len  : NATURAL range 0 to 255 := 0;
  BEGIN
  IF RISING_EDGE(CLK) THEN
    Gen_Load_We <= '0';
    Gen_Start <= '0';
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
        Start_Offset <= Max_Samples;
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
        inputs_prev <= Inputs;
    ELSE
      IF (Full = '1') THEN
        CASE (Thread23) IS
          WHEN 0 =>
            addr <= 0;
            Thread23 := 1;
          WHEN 1 =>
            IF ( addr < Samples) THEN 
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
                        IF (UART_TX_Busy = '0') THEN
                        ELSE
                          Thread31 := Thread31 + 1;
                        END IF;
                      WHEN 2 =>
                        UART_TX_Enable <= '0';
                        Thread31 := 3;
                      WHEN 3 =>
                        IF (UART_TX_Busy = '1') THEN
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
        WHEN others => Thread30 := 0;
      END CASE;
    WHEN 33 =>
      addr <= addr + 1;
      Thread26 := 0;
      Thread23 := 1;
              WHEN others => Thread26 := 0;
            END CASE;
          WHEN 3 =>
            Run_OLS <= '0';
            Run <= '0';
            Thread23 := 0;
          WHEN others => Thread23 := 0;
        END CASE;
      END IF;
    END IF;
    CASE (Thread38) IS
      WHEN 0 =>
        Thread38 := 1;
      WHEN 1 =>
        IF (UART_RX_Busy = '0') THEN
        ELSE
          Thread38 := Thread38 + 1;
        END IF;
      WHEN 2 =>
        IF (UART_RX_Busy = '1') THEN
        ELSE
          Thread38 := Thread38 + 1;
        END IF;
      WHEN 3 =>
        IF (blk_mode = '1') THEN
          Gen_Load_Byte <= UART_RX_Data;
          gen_load_cnt <= 1;
          IF (blk_len > 0) THEN
            blk_len := blk_len - 1;
            blk_len_s <= blk_len;
          END IF;
          IF (blk_len = 0) THEN
            blk_mode <= '0';
          END IF;
          Thread38 := 0;
        ELSE
          command <= UART_RX_Data;
          Thread38 := 4;
        END IF;
      WHEN 4 =>
        IF (command(7) = '0') THEN 
          Thread38 := Thread38 + 1;
        ELSE
          Thread38 := Thread38 + 2;
        END IF;
      WHEN (4+1) =>
        CASE (Thread44) IS
          WHEN 0 =>
            CASE (command) IS
              WHEN x"00" =>
                Thread44 := Thread44 + 1;
              WHEN x"01" =>
                Thread44 := Thread44 + 2;
              WHEN x"02" =>
                Thread44 := Thread44 + 3;
              WHEN x"04" =>
                Thread44 := 18;
              WHEN x"05" =>
                Thread44 := Thread44 + 7;  -- blk_mode entry
              WHEN x"06" =>
                Thread44 := Thread44 + 8;  -- proto select
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
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 2 =>
            Run_OLS <= '1';
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
                        IF (UART_TX_Busy = '0') THEN
                        ELSE
                          Thread51 := Thread51 + 1;
                        END IF;
                      WHEN 2 =>
                        UART_TX_Enable <= '0';
                        Thread51 := 3;
                      WHEN 3 =>
                        IF (UART_TX_Busy = '1') THEN
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
            CASE (Thread49) IS
              WHEN 0 =>
                wr_ctr <= 18;
                Thread49 := 1;
              WHEN 1 =>
                IF (wr_ctr > 0) THEN
                  Thread49 := Thread49 + 1;
                ELSE
                  Thread44 := 0;
                  Thread45 := 0;
                  Thread49 := 0;
                  Thread38 := 0;
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
                      WHEN 12 => UART_TX_Data <= x"08";
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
                    IF (UART_TX_Busy = '0') THEN
                    ELSE
                      Thread51 := Thread51 + 1;
                    END IF;
                  WHEN 2 =>
                    UART_TX_Enable <= '0';
                    Thread51 := 3;
                  WHEN 3 =>
                    IF (UART_TX_Busy = '1') THEN
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
          WHEN others => Thread44 := 0;
        END CASE;
      WHEN 6 =>
        CASE (Thread44) IS
          WHEN 0 to 1 =>
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
                IF (UART_RX_Busy = '0') THEN
                ELSE
                  Thread45 := Thread45 + 1;
                END IF;
              WHEN 2 =>
                IF (UART_RX_Busy = '1') THEN
                ELSE
                  Thread45 := Thread45 + 1;
                END IF;
              WHEN 3 =>
                data((ctr+1)*8-1 downto ctr*8) <= UART_RX_Data;


                 ctr := ctr + 1;
                Thread45 := 0;
                Thread44 := 3;
              WHEN others => Thread45 := 0;
            END CASE;
          WHEN 5 to 6 =>
            Thread44 := Thread44 + 1;
          WHEN 7 =>
            CASE (command) IS
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
              WHEN others =>
                Thread44 := Thread44 + 10;
            END CASE;
          WHEN 8 =>
            Trigger_Mask   <= data;
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 9 =>
            Trigger_Values <= data;
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 10 =>
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 11 =>
            null;
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 12 =>
            Divider <= TO_INTEGER(UNSIGNED(data(23 downto 0)));
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 13 =>
            Read_Count  <= TO_INTEGER(UNSIGNED(data(15 downto 0)));
          Delay_Count <= TO_INTEGER(UNSIGNED(data(31 downto 16)));
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 14 =>
            Channel_Groups <= data(5 downto 2);
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 15 =>
            Delay_Count <= TO_INTEGER(UNSIGNED(data(29 downto 0)));
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 16 =>
            Read_Count  <= TO_INTEGER(UNSIGNED(data(29 downto 0)));
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 17 =>
            null;
            Thread38 := 0;
                Thread44 := 0;
                Thread45 := 0;
          WHEN 18 =>
            Gen_Load_Byte <= data(7 downto 0);
            gen_load_cnt <= 1;
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 19 =>
            gen_start_cnt <= 2;
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 20 =>
            Gen_Baud_Div <= data(15 downto 0);
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 21 =>
            blk_mode <= '1';
            blk_len := TO_INTEGER(UNSIGNED(data(7 downto 0)));
            blk_len_s <= blk_len;
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 22 =>
            Gen_Proto <= data(0);
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 23 =>
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 24 =>
            gen_tx_pin_int <= TO_INTEGER(UNSIGNED(data(7 downto 0))) mod 8;
            gen_scl_pin_int <= TO_INTEGER(UNSIGNED(data(15 downto 8))) mod 8;
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 25 =>
            gen_i2c_test_int <= data(0);
            gen_i2c_rd_len_int <= TO_INTEGER(UNSIGNED(data(15 downto 8)));
            gen_i2c_dev_r_int <= data(23 downto 16);
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 26 =>
            fast_mode_i <= data(0);
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN 27 =>
            proto_trig_enable   <= data(15);
            proto_trig_protocol <= data(13 downto 12);
            proto_trig_match    <= data(7 downto 0);
            proto_trig_channel  <= TO_INTEGER(UNSIGNED(data(10 downto 8)));
            proto_trig_bauddiv  <= TO_INTEGER(UNSIGNED(data(31 downto 16)));
            Thread44 := 0;
                Thread45 := 0;
                Thread38 := 0;
          WHEN others => Thread44 := 0;
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

  Gen_TX_Pin  <= gen_tx_pin_int;
  Gen_SCL_Pin <= gen_scl_pin_int;
  Gen_I2C_Rd_Len <= gen_i2c_rd_len_int;
  Gen_I2C_Dev_R  <= gen_i2c_dev_r_int;
  Gen_I2C_Test   <= gen_i2c_test_int;
  Fast_Mode      <= fast_mode_i;
  Armed          <= Run_OLS;

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
  
END BEHAVIORAL;

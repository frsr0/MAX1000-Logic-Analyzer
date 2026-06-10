  
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all; 


ENTITY OLS_Logic_Analyzer IS
  GENERIC (
      CLK_Frequency   : INTEGER := 12000000;     
    Max_Samples     : NATURAL := 1000000;      
    Channels        : NATURAL := 4;
    Sim             : boolean := false

  );
PORT (
  CLK : IN STD_LOGIC;
  FAST_CLK : IN STD_LOGIC := '0';
  Inputs_Sys         : IN  STD_LOGIC_VECTOR(Channels-1 downto 0);
  Inputs_Fast        : IN  STD_LOGIC_VECTOR(Channels-1 downto 0);
  SPI_CS             : IN  STD_LOGIC := '1';
  SPI_SCK            : IN  STD_LOGIC := '0';
  SPI_MOSI           : IN  STD_LOGIC := '0';
  SPI_MISO           : OUT STD_LOGIC := 'Z';
  Interface_Mode     : OUT STD_LOGIC := '1';
  sdram_addr  : OUT std_logic_vector (11 downto 0);
  sdram_ba    : OUT std_logic_vector (1 downto 0);
  sdram_cas_n : OUT std_logic;
  sdram_dq    : INOUT std_logic_vector (15 downto 0) := (others => '0');
  sdram_dqm   : OUT std_logic_vector (1 downto 0);
  sdram_ras_n : OUT std_logic;
  sdram_we_n  : OUT std_logic;
  sdram_cke   : OUT std_logic := '1';
  sdram_cs_n  : OUT std_logic := '0';
  sdram_clk   : OUT std_logic;
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
    Analog_Mode    : OUT STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
    Analog_Ch0     : OUT NATURAL range 0 to 15 := 0;
    Analog_Ch1     : OUT NATURAL range 0 to 15 := 1;
    Status        : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Continuous_Mode : OUT STD_LOGIC := '0';
    Buffer_Full     : IN  STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
    Buffer_Ack      : OUT STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
    Analog_Frame_Data : IN STD_LOGIC_VECTOR(127 downto 0) := (others => '0');
    Analog_Frame_Len  : IN NATURAL range 1 to 14 := 1;
    Analog_Stream_Mode : IN STD_LOGIC := '0';
    Pin_Map_Write  : OUT STD_LOGIC := '0';
    Pin_Map_Channel : OUT NATURAL range 0 to 15 := 0;
    Pin_Map_Pin     : OUT NATURAL range 0 to 31 := 0;
    Debug_Ch0_Enable : OUT STD_LOGIC := '0';
    Schmitt_Enable   : OUT STD_LOGIC := '0';
    Schmitt_Threshold : OUT NATURAL range 0 to 7 := 3;
    Gen_Start_Ack    : IN  STD_LOGIC := '0';
    Gen_Start_Reject : IN  STD_LOGIC := '0';
    Gen_Done_Pulse   : IN  STD_LOGIC := '0';
    Gen_Capture_Active : OUT STD_LOGIC := '0'
);
END OLS_Logic_Analyzer;

ARCHITECTURE BEHAVIORAL OF OLS_Logic_Analyzer IS

  CONSTANT sub_steps    : NATURAL := 16 / Channels;
  SIGNAL OLS_Interface_Rate_Div      : NATURAL          range 1 to 150000000 := 12;
  SIGNAL OLS_Interface_Samples       : NATURAL          range 1 to Max_Samples := Max_Samples;
  SIGNAL OLS_Interface_Start_Offset  : NATURAL          range 0 to Max_Samples := 0;
  SIGNAL OLS_Interface_Run           : STD_LOGIC := '0';
  SIGNAL OLS_Interface_Full          : STD_LOGIC := '0';
  SIGNAL OLS_Interface_Address       : NATURAL          range 0 to Max_Samples-1 := 0;
  SIGNAL OLS_Interface_Outputs       : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL OLS_Interface_Inputs        : STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
  SIGNAL LA_Out : STD_LOGIC_VECTOR(15 downto 0);
  SIGNAL Fast_Logic_Analyzer_SDRAM_CLK_150      : STD_LOGIC;
  SIGNAL LA_Address       : NATURAL          range 0 to Max_Samples := 0;
  SIGNAL Gen_Load_Byte_i    : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL Gen_Load_We_i      : STD_LOGIC := '0';
  SIGNAL Gen_Start_i        : STD_LOGIC := '0';
  SIGNAL Gen_Baud_Div_i     : STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
  SIGNAL Gen_Proto_i       : STD_LOGIC := '0';
  SIGNAL Gen_Busy_i         : STD_LOGIC := '0';
  SIGNAL Gen_TX_Pin_i       : NATURAL range 0 to 31 := 0;
  SIGNAL Gen_SCL_Pin_i      : NATURAL range 0 to 31 := 0;
  SIGNAL gen_i2c_rd_len_i    : NATURAL range 0 to 255 := 0;
  SIGNAL gen_i2c_dev_r_i     : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL gen_i2c_test_i      : STD_LOGIC := '0';
  SIGNAL gen_spi_test_i      : STD_LOGIC := '0';
  SIGNAL armed_i             : STD_LOGIC := '0';
  SIGNAL fast_mode_i         : STD_LOGIC := '0';
  SIGNAL continuous_mode_i   : STD_LOGIC := '0';
  SIGNAL buffer_full_i       : STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
  SIGNAL buffer_ack_i        : STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
  SIGNAL fla_status          : STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  SIGNAL analog_mode_i       : STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
  SIGNAL analog_ch0_i        : NATURAL range 0 to 15 := 0;
  SIGNAL analog_ch1_i        : NATURAL range 0 to 15 := 1;
  SIGNAL pin_map_write_i     : STD_LOGIC := '0';
  SIGNAL pin_map_channel_i   : NATURAL range 0 to 15 := 0;
  SIGNAL pin_map_pin_i       : NATURAL range 0 to 31 := 0;
  SIGNAL debug_ch0_enable_i  : STD_LOGIC := '0';
  SIGNAL schmitt_enable_i    : STD_LOGIC := '0';
  SIGNAL schmitt_threshold_i : NATURAL range 0 to 7 := 3;
  SIGNAL gen_capture_active_i : STD_LOGIC := '0';
  SIGNAL gen_start_ack_i      : STD_LOGIC := '0';
  SIGNAL gen_start_reject_i   : STD_LOGIC := '0';
  SIGNAL gen_done_pulse_i     : STD_LOGIC := '0';
  COMPONENT OLS_Interface IS
  GENERIC (
      CLK_Frequency   :   INTEGER     := 12000000;    
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
  Gen_Fifo_Count : IN STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
  Gen_Proto     : OUT STD_LOGIC := '0';
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
      END COMPONENT;
    COMPONENT Fast_Logic_Analyzer_SDRAM IS
  GENERIC (
      Max_Samples    : NATURAL := 3000000;
    Channels       : NATURAL range 1 to 16 := 16;
    Sim            : boolean := false;
    Write_Latency  : natural := 10;
    Read_Latency   : natural := 3;
    Page_Latency   : natural := 3
  );
  PORT (
    CLK : IN STD_LOGIC;
    CLK_150     : OUT STD_LOGIC;
    Rate_Div     : IN  NATURAL range 1 to 150000000 := 12; 
    Samples      : IN  NATURAL range 1 to Max_Samples   := Max_Samples;  
    Start_Offset : IN  NATURAL range 0 to Max_Samples   := 0;  
    Run         : IN  STD_LOGIC := '0'; 
    Full        : OUT STD_LOGIC := '0'; 
    Inputs      : IN  STD_LOGIC_VECTOR(Channels-1 downto 0) := (others => '0');
    Address     : IN  NATURAL range 0 to Max_Samples := 0;   
    Outputs     : OUT STD_LOGIC_VECTOR(15 downto 0); 
    sdram_addr  : OUT std_logic_vector (11 downto 0);
    sdram_ba    : OUT std_logic_vector (1 downto 0);
    sdram_cas_n : OUT std_logic;
    sdram_dq    : INOUT std_logic_vector (15 downto 0) := (others => '0');
    sdram_dqm   : OUT std_logic_vector (1 downto 0);
    sdram_ras_n : OUT std_logic;
    sdram_we_n  : OUT std_logic;
    sdram_cke   : OUT std_logic := '1';
    sdram_cs_n  : OUT std_logic := '0';
    sdram_clk   : OUT std_logic;
    Status      : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    s_burst     : OUT std_logic := '0';
    Armed       : IN  std_logic := '0';
    Fast_Mode   : IN  std_logic := '0';
     FAST_CLK    : IN  std_logic := '0';
     Continuous_Mode : IN  std_logic := '0';
     Buffer_Full     : OUT STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
     Buffer_Ack      : IN  STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
     Analog_Frame_Data : IN STD_LOGIC_VECTOR(127 downto 0) := (others => '0');
      Analog_Frame_Len  : IN NATURAL range 1 to 14 := 1;
     Analog_Stream_Mode : IN STD_LOGIC := '0'

   );
   END COMPONENT;
  
BEGIN

  OLS_Interface_Inputs(Channels-1 downto 0) <= Inputs_Sys;

  OLS_Interface_Outputs(Channels-1 downto 0) <= LA_Out(((OLS_Interface_Address mod sub_steps + 1)*Channels)-1 downto (OLS_Interface_Address mod sub_steps)*Channels);
  LA_Address <= OLS_Interface_Address/sub_steps;
  Gen_Load_Byte <= Gen_Load_Byte_i;
  Gen_Load_We   <= Gen_Load_We_i;
  Gen_Start     <= Gen_Start_i;
  Gen_Baud_Div  <= Gen_Baud_Div_i;
  Gen_Busy_i    <= Gen_Busy;
  Gen_Proto <= Gen_Proto_i;
  Gen_TX_Pin  <= Gen_TX_Pin_i;
  Gen_SCL_Pin <= Gen_SCL_Pin_i;
  Gen_I2C_Rd_Len <= gen_i2c_rd_len_i;
  Gen_I2C_Dev_R  <= gen_i2c_dev_r_i;
  Gen_I2C_Test   <= gen_i2c_test_i;
  Gen_SPI_Test   <= gen_spi_test_i;
  Armed          <= armed_i;
  Fast_Mode      <= fast_mode_i;
  Analog_Mode <= analog_mode_i;
  Analog_Ch0 <= analog_ch0_i;
  Analog_Ch1 <= analog_ch1_i;
  Status <= fla_status;
  Continuous_Mode <= continuous_mode_i;
  Buffer_Ack <= buffer_ack_i;
  Pin_Map_Write <= pin_map_write_i;
  Pin_Map_Channel <= pin_map_channel_i;
  Pin_Map_Pin <= pin_map_pin_i;
  Debug_Ch0_Enable <= debug_ch0_enable_i;
  Schmitt_Enable   <= schmitt_enable_i;
  Schmitt_Threshold <= schmitt_threshold_i;
  Gen_Capture_Active <= gen_capture_active_i;
  OLS_Interface1 : OLS_Interface
  GENERIC MAP (
      CLK_Frequency => CLK_Frequency,Max_Samples   => Max_Samples
  ) PORT MAP (
    CLK           => CLK,
    FAST_CLK      => FAST_CLK,
    SPI_CS        => SPI_CS,SPI_SCK       => SPI_SCK,SPI_MOSI      => SPI_MOSI,SPI_MISO      => SPI_MISO,Interface_Mode=> Interface_Mode,Inputs        => OLS_Interface_Inputs,Rate_Div      => OLS_Interface_Rate_Div,Samples       => OLS_Interface_Samples,Start_Offset  => OLS_Interface_Start_Offset,Run           => OLS_Interface_Run,Full          => OLS_Interface_Full,Address       => OLS_Interface_Address,Outputs       => OLS_Interface_Outputs,
    Gen_Load_Byte => Gen_Load_Byte_i,Gen_Load_We   => Gen_Load_We_i,Gen_Start     => Gen_Start_i,Gen_Baud_Div  => Gen_Baud_Div_i,Gen_Busy      => Gen_Busy_i,Gen_Fifo_Count => Gen_Fifo_Count,Gen_Proto     => Gen_Proto_i,
    Gen_TX_Pin    => Gen_TX_Pin_i,Gen_SCL_Pin   => Gen_SCL_Pin_i,
    Gen_I2C_Rd_Len => gen_i2c_rd_len_i,Gen_I2C_Dev_R  => gen_i2c_dev_r_i,    Gen_I2C_Test   => gen_i2c_test_i,
    Gen_SPI_Test   => gen_spi_test_i,
    Armed          => armed_i,
    Fast_Mode      => fast_mode_i,
    Analog_Mode    => analog_mode_i,
    Analog_Ch0     => analog_ch0_i,
    Analog_Ch1     => analog_ch1_i,
    Continuous_Mode => continuous_mode_i,
    Buffer_Full     => buffer_full_i,
    Buffer_Ack      => buffer_ack_i,
    Pin_Map_Write  => pin_map_write_i,
    Pin_Map_Channel => pin_map_channel_i,
    Pin_Map_Pin     => pin_map_pin_i,
    Debug_Ch0_Enable => debug_ch0_enable_i,
    Schmitt_Enable   => schmitt_enable_i,
    Schmitt_Threshold => schmitt_threshold_i,
    Gen_Capture_Active => gen_capture_active_i,
    Gen_Start_Ack      => gen_start_ack_i,
    Gen_Start_Reject   => gen_start_reject_i,
    Gen_Done_Pulse     => gen_done_pulse_i
    
  );
  Fast_Logic_Analyzer_SDRAM1 : Fast_Logic_Analyzer_SDRAM
  GENERIC MAP (
      Max_Samples  => Max_Samples,Channels     => Channels,Sim          => Sim
  ) PORT MAP (
    CLK => CLK,
    CLK_150      => Fast_Logic_Analyzer_SDRAM_CLK_150,Rate_Div     => OLS_Interface_Rate_Div,Samples      => OLS_Interface_Samples,Start_Offset => OLS_Interface_Start_Offset,Run          => OLS_Interface_Run,Full         => OLS_Interface_Full,Inputs       => Inputs_Fast,Address      => LA_Address,Outputs      => LA_Out,sdram_addr   => sdram_addr,sdram_ba     => sdram_ba,sdram_cas_n  => sdram_cas_n,sdram_dq     => sdram_dq,sdram_dqm    => sdram_dqm,sdram_ras_n  => sdram_ras_n,sdram_we_n   => sdram_we_n,    sdram_cke    => sdram_cke,sdram_cs_n   => sdram_cs_n,sdram_clk    => sdram_clk,
    Status       => fla_status,
    Armed        => armed_i,
    Fast_Mode    => fast_mode_i,
    FAST_CLK     => FAST_CLK,
    Continuous_Mode => continuous_mode_i,
    Buffer_Full     => buffer_full_i,
    Buffer_Ack      => buffer_ack_i,
    Analog_Frame_Data => Analog_Frame_Data,
    Analog_Frame_Len  => Analog_Frame_Len,
    Analog_Stream_Mode => Analog_Stream_Mode
  );
  
END BEHAVIORAL;

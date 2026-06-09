  
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all; 


ENTITY SDRAM_Interface IS
  GENERIC (
      Sim : BOOLEAN := false;
      Write_Latency : natural := 10;
      Read_Latency  : natural := 3;
      Page_Latency  : natural := 3
  );
PORT (
  CLK : IN STD_LOGIC;
  Reset                 : IN    std_logic                     := '0';
  CLK_150_Out           : OUT   std_logic;                                        
  Address               : IN    std_logic_vector(21 downto 0) := (others => '0'); 
  Write_Enable          : IN    std_logic                     := '0';             
  Write_Data            : IN    std_logic_vector(15 downto 0) := (others => '0'); 
  Burst                 : IN    std_logic                     := '0';
  Read_Enable           : IN    std_logic                     := '0';             
  Read_Data             : OUT   std_logic_vector(15 downto 0) := (others => '0'); 
   Read_Valid            : OUT   std_logic                     := '0';             
   Busy                  : OUT   std_logic                     := '0';             
   Idle                  : OUT   std_logic                     := '0';             
   sdram_addr            : out   std_logic_vector(11 downto 0);
  sdram_ba              : out   std_logic_vector(1 downto 0);
  sdram_cas_n           : out   std_logic;
  sdram_cke             : out   std_logic := '1';  
  sdram_cs_n            : out   std_logic := '0';  
  sdram_dq              : inout std_logic_vector(15 downto 0) := (others => '0');
  sdram_dqm             : out   std_logic_vector(1 downto 0);
  sdram_ras_n           : out   std_logic;
  sdram_we_n            : out   std_logic;
  sdram_clk             : out   std_logic

);
END SDRAM_Interface;

ARCHITECTURE BEHAVIORAL OF SDRAM_Interface IS

  component SDRAM_Controller is
  generic (
    CLK_Frequency : natural := 96000000
  );
  port (
  sdram_addr            : out   std_logic_vector(11 downto 0);                    
  sdram_ba              : out   std_logic_vector(1 downto 0);                     
  sdram_cas_n           : out   std_logic;                                        
  sdram_cke             : out   std_logic;                                        
  sdram_cs_n            : out   std_logic;                                        
  sdram_dq              : inout std_logic_vector(15 downto 0) := (others => 'X'); 
  sdram_dqm             : out   std_logic_vector(1 downto 0);                     
  sdram_ras_n           : out   std_logic;                                        
  sdram_we_n            : out   std_logic;                                        
  sdram_s_address       : in    std_logic_vector(21 downto 0) := (others => 'X'); 
  sdram_s_byteenable_n  : in    std_logic_vector(1 downto 0)  := (others => 'X'); 
  sdram_s_chipselect    : in    std_logic                     := 'X';             
  sdram_s_writedata     : in    std_logic_vector(15 downto 0) := (others => 'X'); 
  sdram_s_read_n        : in    std_logic                     := 'X';             
   sdram_s_write_n       : in    std_logic                     := 'X';
   sdram_s_burst         : in    std_logic                     := 'X';
  sdram_s_readdata      : out   std_logic_vector(15 downto 0);                    
  sdram_s_readdatavalid : out   std_logic;                                        
   sdram_s_waitrequest   : out   std_logic;                                        
   sdram_s_idle          : out   std_logic;                                        
   reset_reset_n         : in    std_logic                     := 'X';             
  clk_in_clk            : in    std_logic                     := 'X'              
  );
  end component SDRAM_Controller;


  SIGNAL reset_reset_n : STD_LOGIC;
  SIGNAL sdram_s_address       : std_logic_vector(21 downto 0) := (others => '0');
  SIGNAL sdram_s_byteenable_n  : std_logic_vector(1 downto 0)  := (others => '0');
  SIGNAL sdram_s_chipselect    : std_logic                     := '1';
  SIGNAL sdram_s_writedata     : std_logic_vector(15 downto 0) := (others => '0');
  SIGNAL sdram_s_read_n        : std_logic                     := '1';
  SIGNAL sdram_s_write_n       : std_logic                     := '1';
  SIGNAL sdram_s_burst         : std_logic                     := '0';
  SIGNAL sdram_s_readdata      : std_logic_vector(15 downto 0);
  SIGNAL sdram_s_readdatavalid : std_logic;
   SIGNAL sdram_s_waitrequest   : std_logic;
   SIGNAL sdram_s_idle          : std_logic;
  TYPE sdram_type IS ARRAY (0 to 4095) OF STD_LOGIC_VECTOR(15 downto 0);
  SIGNAL sdram_ram : sdram_type;
  SIGNAL reset_cnt : natural range 0 to 1048575 := 0;
  SIGNAL sdram_reset_n : std_logic := '0';

BEGIN

  sdram_s_address <= Address;
  sdram_s_write_n <= NOT Write_Enable;
  sdram_s_writedata <= Write_Data;
  sdram_s_burst <= Burst;
  sdram_s_read_n <= NOT Read_Enable;
  Read_Data <= sdram_s_readdata;
  Read_Valid <= sdram_s_readdatavalid;
   Busy <= sdram_s_waitrequest;
   Idle <= sdram_s_idle;

  -- SDRAM controller reset: hold low for ~100us after power-up,
  -- then release so the controller runs its init sequence properly.
  process(CLK)
  begin
    if rising_edge(CLK) then
      if reset_cnt < 480000 then  -- 480000 cycles @ 96 MHz = 5 ms (100 us min needed)
        reset_cnt <= reset_cnt + 1;
        sdram_reset_n <= '0';
      else
        sdram_reset_n <= '1';
      end if;
    end if;
  end process;

   u187: if NOT sim generate
   reset_reset_n <= sdram_reset_n;
  CLK_150_Out <= CLK;    -- 96 MHz core clock from PLL
  sdram_clk <= CLK;
  sdram_s_byteenable_n <= (others => '0');
  sdram_s_chipselect <= '1';
   u0 : component SDRAM_Controller
  port map (
  sdram_addr            => sdram_addr,            
  sdram_ba              => sdram_ba,              
  sdram_cas_n           => sdram_cas_n,           
  sdram_cke             => sdram_cke,             
  sdram_cs_n            => sdram_cs_n,            
  sdram_dq              => sdram_dq,              
  sdram_dqm             => sdram_dqm,             
  sdram_ras_n           => sdram_ras_n,           
  sdram_we_n            => sdram_we_n,            
  sdram_s_address       => sdram_s_address,       
  sdram_s_byteenable_n  => sdram_s_byteenable_n,  
  sdram_s_chipselect    => sdram_s_chipselect,    
  sdram_s_writedata     => sdram_s_writedata,     
  sdram_s_read_n        => sdram_s_read_n,        
   sdram_s_write_n       => sdram_s_write_n,
   sdram_s_burst         => sdram_s_burst,
   sdram_s_readdata      => sdram_s_readdata,
   sdram_s_readdatavalid => sdram_s_readdatavalid, 
   sdram_s_waitrequest   => sdram_s_waitrequest,   
   sdram_s_idle          => sdram_s_idle,
   reset_reset_n         => reset_reset_n,
  clk_in_clk            => CLK
  );
  end generate;
  Generate1 : if sim GENERATE
   reset_reset_n <= NOT Reset;
     CLK_150_Out <= CLK;
     sdram_clk <= CLK;
     PROCESS (CLK)
       VARIABLE wait_r : BOOLEAN := false;
     BEGIN
       IF (rising_edge(CLK)) THEN
         sdram_s_idle <= '0';
         IF (sdram_s_read_n = '0') THEN
           IF (not wait_r) THEN
             IF (sdram_s_waitrequest = '0') THEN
               sdram_s_waitrequest <= '1';
             ELSE
               sdram_s_waitrequest <= '0';
               sdram_s_readdatavalid <= '1';
                sdram_s_readdata <= sdram_ram(TO_INTEGER(UNSIGNED(sdram_s_address)) mod 4096);
                wait_r := true;
             END IF;
           ELSE
             sdram_s_readdatavalid <= '0';
           END IF;
          ELSIF (sdram_s_write_n = '0') THEN
            IF (not wait_r) THEN
              IF (sdram_s_waitrequest = '0') THEN
                sdram_s_waitrequest <= '1';
                sdram_ram(TO_INTEGER(UNSIGNED(sdram_s_address)) mod 4096) <= sdram_s_writedata;
             ELSE
               sdram_s_waitrequest <= '0';
               wait_r := true;
             END IF;
           END IF;
         ELSE
           sdram_s_readdatavalid <= '0';
           sdram_s_waitrequest <= '0';
           sdram_s_idle <= '1';
           wait_r := false;
         END IF;
       END IF;
     END PROCESS;
  END GENERATE Generate1;
  
END BEHAVIORAL;

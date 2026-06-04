library ieee;
use ieee.std_logic_1164.all;

entity SDRAM_Controller is
  generic (CLK_Frequency : natural := 12000000);
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
end SDRAM_Controller;

architecture stub of SDRAM_Controller is
begin
  sdram_s_readdata      <= (others => '0');
  sdram_s_readdatavalid <= '0';
  sdram_s_waitrequest   <= '0';
  sdram_s_idle          <= '1';
end stub;

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;

entity SDRAM_PLL is
  port (
    areset  : in  std_logic := '0';
    inclk0  : in  std_logic := '0';
    c0      : out std_logic;
    c1      : out std_logic;
    c2      : out std_logic;
    locked  : out std_logic
  );
end SDRAM_PLL;

architecture sim of SDRAM_PLL is
  component PLL_Model is
    generic (
      INPUT_FREQ   : real := 12.0e6;
      MULTIPLY_BY  : natural := 8;
      DIVIDE_BY    : natural := 1;
      FAST_MULT    : natural := 10;
      FAST_DIV     : natural := 1;
      SDRAM_MULT   : natural := 8;
      SDRAM_DIV    : natural := 1;
      LOCK_CYCLES  : natural := 100
    );
    port (
      inclk0  : in  std_logic;
      c0      : out std_logic;
      c1      : out std_logic;
      c2      : out std_logic;
      locked  : out std_logic
    );
  end component;
begin
  pll : PLL_Model
    generic map (
      INPUT_FREQ  => 12.0e6,
      MULTIPLY_BY => 50,
      DIVIDE_BY   => 6,
      FAST_MULT   => 50,
      FAST_DIV    => 3,
      SDRAM_MULT  => 50,
      SDRAM_DIV   => 6
    )
    port map (
      inclk0 => inclk0,
      c0     => c0,
      c1     => c1,
      c2     => c2,
      locked => locked
    );
end sim;

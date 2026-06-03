library ieee;
use ieee.std_logic_1164.all;

entity SDRAM_PLL is
  generic (
    MULTIPLY_BY : positive := 1;
    DIVIDE_BY   : positive := 1
  );
  port (
    inclk0 : in  std_logic;
    c0     : out std_logic;
    c1     : out std_logic;
    locked : out std_logic
  );
end SDRAM_PLL;

architecture sim of SDRAM_PLL is
  constant INPUT_FREQ : real := 12.0e6;
  signal sysclk : std_logic;
  signal lock   : std_logic;
begin
  pll : entity work.pll_model
    generic map (MULTIPLY_BY => MULTIPLY_BY, DIVIDE_BY => DIVIDE_BY, INPUT_FREQ => INPUT_FREQ)
    port map (inclk0 => inclk0, c0 => sysclk, locked => lock);

  c0 <= sysclk;
  c1 <= sysclk;
  locked <= lock;
end sim;

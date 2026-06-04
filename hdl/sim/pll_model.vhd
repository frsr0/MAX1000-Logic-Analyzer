library ieee;
use ieee.std_logic_1164.all;

entity pll_model is
  generic (
    MULTIPLY_BY : positive := 1;
    DIVIDE_BY   : positive := 1;
    INPUT_FREQ  : real     := 12.0e6
  );
  port (
    inclk0 : in  std_logic;
    c0     : out std_logic := '0';
    locked : out std_logic := '0'
  );
end pll_model;

architecture sim of pll_model is
  constant LOCK_CYCLES : natural := 100;
  signal lock_cnt : natural := 0;
begin
  process(inclk0)
  begin
    if rising_edge(inclk0) then
      if lock_cnt < LOCK_CYCLES then
        lock_cnt <= lock_cnt + 1;
      end if;
    end if;
  end process;

  locked <= '1' when lock_cnt >= LOCK_CYCLES else '0';

  process
    variable period : time;
    variable half   : time;
  begin
    wait until lock_cnt >= LOCK_CYCLES;
    period := (1 sec) * real(DIVIDE_BY) / (real(MULTIPLY_BY) * INPUT_FREQ);
    half   := period / 2;
    loop
      c0 <= '0';
      wait for half;
      c0 <= '1';
      wait for half;
    end loop;
  end process;
end sim;

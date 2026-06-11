library IEEE;
use IEEE.STD_LOGIC_1164.ALL;

entity PLL_Model is
  generic (
    INPUT_FREQ   : real    := 12.0e6;  -- 12 MHz input
    MULTIPLY_BY  : natural := 50;      -- c0 = 100 MHz
    DIVIDE_BY    : natural := 6;
    FAST_MULT    : natural := 50;      -- c1 = 200 MHz
    FAST_DIV     : natural := 3;
    SDRAM_MULT   : natural := 50;      -- c2 = 100 MHz @ -90 degrees
    SDRAM_DIV    : natural := 6;
    LOCK_CYCLES  : natural := 100
  );
  port (
    inclk0 : in  std_logic;
    c0     : out std_logic := '0';
    c1     : out std_logic := '0';
    c2     : out std_logic := '0';
    locked : out std_logic := '0'
  );
end PLL_Model;

architecture sim of PLL_Model is
  constant input_period : time := 1 sec / INPUT_FREQ;

  constant c0_period : time := input_period * real(DIVIDE_BY) / real(MULTIPLY_BY);
  constant c1_period : time := input_period * real(FAST_DIV) / real(FAST_MULT);
  constant c2_period : time := input_period * real(SDRAM_DIV) / real(SDRAM_MULT);
  constant sdram_phase_ns : real := 1.0 / (INPUT_FREQ * real(MULTIPLY_BY) / real(DIVIDE_BY) / 1.0e6) * 250.0;
  constant c2_shift : time := (c2_period * 250) / 1000;  -- -90 degrees

  signal c0_int : std_logic := '0';
  signal c1_int : std_logic := '0';
  signal c2_int : std_logic := '0';
  signal locked_int : std_logic := '0';
  signal lock_count : natural := 0;

begin

  c0 <= c0_int;
  c1 <= c1_int;
  c2 <= c2_int;
  locked <= locked_int;

  process
  begin
    wait on inclk0;
    if rising_edge(inclk0) then
      if lock_count < LOCK_CYCLES then
        lock_count <= lock_count + 1;
      else
        locked_int <= '1';
      end if;
    end if;
  end process;

  process
  begin
    wait until locked_int = '1';
    loop
      c0_int <= '0'; wait for c0_period / 2;
      c0_int <= '1'; wait for c0_period / 2;
    end loop;
  end process;

  process
  begin
    wait until locked_int = '1';
    loop
      c1_int <= '0'; wait for c1_period / 2;
      c1_int <= '1'; wait for c1_period / 2;
    end loop;
  end process;

  process
  begin
    wait until locked_int = '1';
    wait for c2_shift;
    loop
      c2_int <= '0'; wait for c2_period / 2;
      c2_int <= '1'; wait for c2_period / 2;
    end loop;
  end process;

end sim;

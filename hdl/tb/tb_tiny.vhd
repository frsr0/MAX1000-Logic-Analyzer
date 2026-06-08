library IEEE;
use IEEE.STD_LOGIC_1164.ALL;

entity tb_tiny is end;
architecture sim of tb_tiny is
  signal clk : std_logic := '0';
  signal s : std_logic;
  procedure gen_clk(signal s : inout std_logic; half : time) is begin
    loop
      s <= '0'; wait for half;
      s <= '1'; wait for half;
    end loop;
  end procedure;
begin
  gen_clk(clk, 5 ns);
  process begin
    report "TINY_RUNNING"; wait; 
  end process;
end sim;

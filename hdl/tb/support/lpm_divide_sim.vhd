library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity lpm_divide is
  generic (
    LPM_WIDTHN : natural;
    LPM_WIDTHD : natural;
    LPM_NREPRESENTATION : string := "UNSIGNED";
    LPM_DREPRESENTATION : string := "UNSIGNED";
    LPM_PIPELINE : natural := 0
  );
  port (
    clock    : in std_logic;
    numer    : in std_logic_vector(LPM_WIDTHN-1 downto 0);
    denom    : in std_logic_vector(LPM_WIDTHD-1 downto 0);
    quotient : out std_logic_vector(LPM_WIDTHN-1 downto 0);
    remain   : out std_logic_vector(LPM_WIDTHD-1 downto 0)
  );
end entity;

architecture sim of lpm_divide is
begin
  process(clock)
    variable n, d, q, r : integer;
  begin
    if rising_edge(clock) then
      n := to_integer(unsigned(numer));
      d := to_integer(unsigned(denom));
      if d /= 0 then
        q := n / d;
        r := n mod d;
      else
        q := 0;
        r := 0;
      end if;
      quotient <= std_logic_vector(to_unsigned(q, LPM_WIDTHN));
      remain   <= std_logic_vector(to_unsigned(r, LPM_WIDTHD));
    end if;
  end process;
end architecture;

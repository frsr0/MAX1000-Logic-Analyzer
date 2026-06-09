library ieee;
use ieee.std_logic_1164.all;

package lpm_components is
  component lpm_divide
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
  end component;
end package;

package body lpm_components is
end package body;

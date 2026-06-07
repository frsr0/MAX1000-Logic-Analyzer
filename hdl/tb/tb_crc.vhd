library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.spi_protocol_pkg.all;

entity tb_crc is end;
architecture sim of tb_crc is
begin
  process
    variable c : std_logic_vector(15 downto 0);
    variable v : std_logic_vector(7 downto 0) := x"01";
  begin
    report "x'01' ascending=" & boolean'image(v'ascending) &
           " left=" & integer'image(v'left) &
           " right=" & integer'image(v'right) &
           " low=" & integer'image(v'low) &
           " high=" & integer'image(v'high);
    if v'ascending then
      report "  idx = left + b = " & integer'image(v'left) & " + b";
    else
      report "  idx = right + b = " & integer'image(v'right) & " + b";
    end if;
    -- Manually compute CRC step by step to debug
    declare
      variable mcrc : std_logic_vector(15 downto 0) := x"FFFF";
      variable mbit : std_logic;
    begin
      for b in 0 to 7 loop
        mbit := x"01"(b) xor mcrc(0);
        if mbit = '1' then
          mcrc := '0' & mcrc(15 downto 1);
          mcrc := mcrc xor x"A001";
        else
          mcrc := '0' & mcrc(15 downto 1);
        end if;
        report "  manual b=" & integer'image(b) & " crc=" & integer'image(to_integer(unsigned(mcrc)));
      end loop;
      report "Manual CRC after 0x01: " & integer'image(to_integer(unsigned(mcrc)));
    end;
    c := crc16(x"01", x"FFFF");
    report "Function CRC after 0x01: " & integer'image(to_integer(unsigned(c)));
    c := crc16(x"42", c);
    report "CRC after 0x42: " & integer'image(to_integer(unsigned(c)));
    c := crc16(x"00", c);
    report "CRC after 0x00: " & integer'image(to_integer(unsigned(c)));
    c := crc16(x"00", c);
    report "CRC after 0x00: " & integer'image(to_integer(unsigned(c)));
    report "Expected: " & integer'image(52385);
    wait;
  end process;
end sim;

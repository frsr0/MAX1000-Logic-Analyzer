library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.spi_protocol_pkg.all;

entity tb_crc2 is end;
architecture sim of tb_crc2 is
begin
  process
    variable c : integer := 65535;
  begin
    c := crc16_int(1, c);
    report "crc16_int(1)=" & integer'image(c) & "  expected=32894";
    c := crc16_int(66, c);
    report "crc16_int(66)=" & integer'image(c) & "  expected=4480";
    c := crc16_int(0, c);
    report "crc16_int(0)=" & integer'image(c) & "  expected=15232";
    c := crc16_int(0, c);
    report "crc16_int(0)=" & integer'image(c) & "  expected=52385";
    wait;
  end process;
end sim;

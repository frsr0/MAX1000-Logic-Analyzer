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
    assert c = 32894
      report "crc16_int(1) mismatch: got " & integer'image(c) & ", expected 32894" severity failure;
    c := crc16_int(66, c);
    report "crc16_int(66)=" & integer'image(c) & "  expected=4480";
    assert c = 4480
      report "crc16_int(66) mismatch: got " & integer'image(c) & ", expected 4480" severity failure;
    c := crc16_int(0, c);
    report "crc16_int(0)=" & integer'image(c) & "  expected=40976";
    assert c = 40976
      report "crc16_int(0) mismatch: got " & integer'image(c) & ", expected 40976" severity failure;
    c := crc16_int(0, c);
    report "crc16_int(0)=" & integer'image(c) & "  expected=52385";
    assert c = 52385
      report "crc16_int(0) mismatch: got " & integer'image(c) & ", expected 52385" severity failure;
    std.env.finish;
    wait;
  end process;
end sim;

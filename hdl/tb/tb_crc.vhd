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
    -- Manual step-by-step CRC (VHDL has no Ada-style 'declare' block inside a
    -- process body, so these live in the process declarative region).
    variable mcrc : std_logic_vector(15 downto 0);
    variable mbit : std_logic;
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
    mcrc := x"FFFF";
    for b in 0 to 7 loop
      mbit := v(b) xor mcrc(0);
      if mbit = '1' then
        mcrc := '0' & mcrc(15 downto 1);
        mcrc := mcrc xor x"A001";
      else
        mcrc := '0' & mcrc(15 downto 1);
      end if;
      report "  manual b=" & integer'image(b) & " crc=" & integer'image(to_integer(unsigned(mcrc)));
    end loop;
    report "Manual CRC after 0x01: " & integer'image(to_integer(unsigned(mcrc)));
    -- crc16 is a range-sensitive helper (it indexes data(data'low+j)): it is
    -- only correct for descending-range vectors, which is how the RTL calls
    -- it (spi_packet_tx passes std_logic_vector(to_unsigned(...))). A bare
    -- literal x"01" has an ascending range, so wrap it like the RTL does.
    c := crc16(std_logic_vector(to_unsigned(1, 8)), x"FFFF");
    report "Function CRC after 0x01: " & integer'image(to_integer(unsigned(c)));
    c := crc16(std_logic_vector(to_unsigned(16#42#, 8)), c);
    report "CRC after 0x42: " & integer'image(to_integer(unsigned(c)));
    c := crc16(std_logic_vector(to_unsigned(0, 8)), c);
    report "CRC after 0x00: " & integer'image(to_integer(unsigned(c)));
    c := crc16(std_logic_vector(to_unsigned(0, 8)), c);
    report "CRC after 0x00: " & integer'image(to_integer(unsigned(c)));
    report "Expected: " & integer'image(52385);
    assert to_integer(unsigned(c)) = 52385
      report "CRC mismatch: function returned " & integer'image(to_integer(unsigned(c))) &
             ", expected 52385" severity failure;
    std.env.finish;
    wait;
  end process;
end sim;

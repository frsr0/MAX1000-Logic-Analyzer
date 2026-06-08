with open('hdl/tb/tb_spi_protocol.vhd', 'r') as f:
    content = f.read()

monitor = """
  -- Monitor rx_ok/rx_err
  process(rx_ok, rx_err) is
  begin
    if rx_ok = '1' then report "rx_ok=1" severity note; end if;
    if rx_err = '1' then report "rx_err=1" severity note; end if;
  end process;
"""

idx = content.find('  -- Test control')
content = content[:idx] + monitor + content[idx:]

with open('hdl/tb/tb_spi_protocol.vhd', 'w') as f:
    f.write(content)
print('Added monitor')

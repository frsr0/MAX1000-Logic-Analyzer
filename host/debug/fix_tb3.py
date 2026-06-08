import re
with open('hdl/tb/tb_spi_protocol.vhd', 'r') as f:
    c = f.read()

poll_code = """wait for CLK_PERIOD;
    for i in 0 to 200 loop
      exit when rx_ok = '1' or rx_err = '1';
      wait for CLK_PERIOD;
    end loop;"""

c = c.replace('wait for 500 ns;', poll_code)
c = c.replace('rx_ok_latched', 'rx_ok')
c = c.replace('rx_err_latched', 'rx_err')

with open('hdl/tb/tb_spi_protocol.vhd', 'w') as f:
    f.write(c)
print('Done')

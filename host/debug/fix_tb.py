import re
with open('hdl/tb/tb_spi_protocol.vhd', 'r') as f:
    c = f.read()
c = c.replace('wait for 400 ns;', 'wait until rx_ok = \'1\' or rx_err = \'1\' for 1 us;')
with open('hdl/tb/tb_spi_protocol.vhd', 'w') as f:
    f.write(c)
print('Done')

import re

# Clean reports from RX
with open('hdl/rtl/spi_packet_rx.vhd', 'r') as f:
    c = f.read()
c = re.sub(r'report "[^"]*" severity note;', '', c)
with open('hdl/rtl/spi_packet_rx.vhd', 'w') as f:
    f.write(c)

# Increase wait times in testbench
with open('hdl/tb/tb_spi_protocol.vhd', 'r') as f:
    t = f.read()
t = t.replace('wait for 200 ns;', 'wait for 500 ns;')
t = t.replace('wait for CLK_PERIOD;', 'wait for CLK_PERIOD;')
with open('hdl/tb/tb_spi_protocol.vhd', 'w') as f:
    f.write(t)
print('Done')
